"""
Incremental scraping: stop when the *last scraped article* from the previous run
appears again on the feed (newest-first). New articles are prepended to the JSON file.

Stop logic:
  1. get_last_scraped_checkpoint() reads data[0] (the newest stored article) — that URL
     is the stop boundary for the next run.
  2. Scrapers walk the live feed top-to-bottom; call is_last_scraped_article() each step.
  3. When it returns True, halt — we've caught up.
  4. Safety cap if checkpoint never appears: 5/15 per section (multi-category)
     or per run (single-feed); see incremental_fetch_limit(per_section=...).
  5. After saving, merge_and_save() / save_replace_only() updates the checkpoint.

Usage:
  from incremental import (
      is_incremental_mode,
      get_last_scraped_checkpoint,
      is_last_scraped_article,
      merge_and_save,
      normalize_link,
  )
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urldefrag, urlparse

# Safety caps when checkpoint never appears on the feed
INCREMENTAL_BOOTSTRAP_LIMIT = 5
INCREMENTAL_RUN_LIMIT = 15
# Per-section/category (Aruna, Island, Lankadeepa, Dinamina, FT.lk, …)
INCREMENTAL_BOOTSTRAP_LIMIT_PER_SECTION = 5
INCREMENTAL_RUN_LIMIT_PER_SECTION = 15


def incremental_fetch_limit(*, bootstrap: bool, per_section: bool = False) -> int:
    """per_section=True: limit applies independently to each category/page."""
    if per_section:
        return (
            INCREMENTAL_BOOTSTRAP_LIMIT_PER_SECTION
            if bootstrap
            else INCREMENTAL_RUN_LIMIT_PER_SECTION
        )
    return INCREMENTAL_BOOTSTRAP_LIMIT if bootstrap else INCREMENTAL_RUN_LIMIT


def reached_incremental_limit(article_count: int, *, bootstrap: bool) -> bool:
    return article_count >= incremental_fetch_limit(bootstrap=bootstrap)


def reached_section_incremental_limit(
    section_article_count: int, *, bootstrap: bool
) -> bool:
    """Safety cap for one category/section in a multi-feed scraper."""
    return section_article_count >= incremental_fetch_limit(
        bootstrap=bootstrap, per_section=True
    )


def is_incremental_mode(argv: list[str] | None = None) -> bool:
    args = argv if argv is not None else sys.argv[1:]
    return "--incremental" in args or os.getenv("SCRAPE_MODE", "").lower() == "incremental"


def normalize_link(url: str) -> str:
    """Canonical URL for dedupe / checkpoint matching."""
    if not url:
        return ""
    url = url.strip()
    url, _ = urldefrag(url)
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    return f"{parsed.scheme}://{parsed.netloc.lower()}{path}"


def _checkpoint_path(articles_json_path: str) -> str:
    base = os.path.splitext(os.path.basename(articles_json_path))[0]
    directory = os.path.dirname(articles_json_path) or "."
    return os.path.join(directory, f"{base}_checkpoint.json")


def _load_articles_list(json_path: str) -> list[dict[str, Any]]:
    if not os.path.isfile(json_path):
        return []
    try:
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def load_checkpoint_state(articles_json_path: str) -> dict[str, Any]:
    cp_path = _checkpoint_path(articles_json_path)
    if not os.path.isfile(cp_path):
        return {}
    try:
        with open(cp_path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def get_section_checkpoint(
    articles_json_path: str,
    section_key: str,
) -> tuple[str | None, str | None]:
    """Per-feed checkpoint (e.g. ftlk top-story/26). Returns normalized (link, title)."""
    sections = load_checkpoint_state(articles_json_path).get("sections") or {}
    entry = sections.get(section_key)
    if isinstance(entry, dict) and entry.get("last_scraped_link"):
        link = normalize_link(entry["last_scraped_link"])
        title = entry.get("last_scraped_title") or ""
        return link, title
    return None, None


def migrate_global_checkpoint_to_sections(
    articles_json_path: str,
    section_keys: list[str],
) -> bool:
    """Fill any section missing a checkpoint from the legacy global checkpoint."""
    if not section_keys:
        return False
    state = load_checkpoint_state(articles_json_path)
    global_link = state.get("last_scraped_link")
    if not global_link:
        return False
    sections = state.get("sections") or {}
    global_title = state.get("last_scraped_title") or ""
    updates: dict[str, tuple[str, str]] = {}
    for key in section_keys:
        entry = sections.get(key)
        if isinstance(entry, dict) and entry.get("last_scraped_link"):
            continue
        updates[key] = (global_link, global_title)
    if not updates:
        return False
    update_section_checkpoints(articles_json_path, updates)
    print(
        f"[INCREMENTAL] Filled {len(updates)} missing section checkpoint(s) from global"
    )
    return True


def _title_for_article_link(
    link: str,
    scraped_articles: list[dict[str, Any]],
) -> str:
    norm = normalize_link(link)
    for article in scraped_articles:
        if normalize_link(article.get("link", "")) == norm:
            return (article.get("title") or "").strip()
    return ""


def title_for_checkpoint_link(
    link: str,
    scraped_articles: list[dict[str, Any]],
    articles_json_path: str,
    *,
    section_checkpoint_link: str | None = None,
    section_checkpoint_title: str | None = None,
) -> str:
    """Title for a checkpoint URL — never reuse another section's title."""
    title = _title_for_article_link(link, scraped_articles)
    if title:
        return title
    if (
        section_checkpoint_link
        and section_checkpoint_title
        and normalize_link(link) == normalize_link(section_checkpoint_link)
    ):
        return section_checkpoint_title.strip()
    for item in _load_articles_list(articles_json_path):
        if not isinstance(item, dict):
            continue
        if normalize_link(item.get("link", "")) == normalize_link(link):
            return (item.get("title") or "").strip()
    return ""


def filter_cross_section_promo_links(
    section_links: dict[str, list[str]],
    *,
    window: int = 10,
    min_sections: int = 2,
) -> dict[str, list[str]]:
    """
    Drop URLs that appear near the top of multiple category pages (site-wide promos).
    """
    from collections import Counter

    counts: Counter[str] = Counter()
    for links in section_links.values():
        seen: set[str] = set()
        for link in links[:window]:
            norm = normalize_link(link)
            if norm and norm not in seen:
                counts[norm] += 1
                seen.add(norm)
    promos = {url for url, n in counts.items() if n >= min_sections}
    if promos:
        print(
            f"[INCREMENTAL] Filtering {len(promos)} cross-section promo URL(s) "
            f"from category feeds"
        )
    return {
        name: [link for link in links if normalize_link(link) not in promos]
        for name, links in section_links.items()
    }


def apply_section_head_checkpoints(
    articles_json_path: str,
    section_links: dict[str, list[str]],
    scraped_articles: list[dict[str, Any]] | None = None,
    *,
    section_keys: list[str] | None = None,
    link_transform: Callable[[str], str] | None = None,
    section_updates: dict[str, tuple[str, str]] | None = None,
) -> int:
    """
    Persist per-section checkpoints. Prefer explicit section_updates from Phase 2
    (stop boundary / newest fetched). Otherwise fall back to links[0] per section.
    """
    keys = section_keys or list(section_links.keys())
    articles = scraped_articles or []
    updates: dict[str, tuple[str, str]] = dict(section_updates or {})

    if not section_updates:
        for name in keys:
            links = section_links.get(name) or []
            if not links:
                continue
            head_url = link_transform(links[0]) if link_transform else links[0]
            head_title = title_for_checkpoint_link(head_url, articles, articles_json_path)
            updates[name] = (head_url, head_title)

    if updates:
        update_section_checkpoints(articles_json_path, updates)
        for name, (url, _) in updates.items():
            print(f"  [CKPT] {name}: {url[:70]}")

    missing = [k for k in keys if k not in updates]
    if missing:
        print(f"  [CKPT] No list links for: {', '.join(missing)}")
    return len(updates)


def update_section_checkpoints(
    articles_json_path: str,
    section_updates: dict[str, tuple[str, str]],
    *,
    global_newest: tuple[str, str] | None = None,
) -> None:
    """Merge per-section boundaries into the checkpoint file."""
    if not section_updates and not global_newest:
        return

    cp_path = _checkpoint_path(articles_json_path)
    state = load_checkpoint_state(articles_json_path)
    sections: dict[str, Any] = dict(state.get("sections") or {})

    for key, (link, title) in section_updates.items():
        if link:
            sections[key] = {
                "last_scraped_link": link,
                "last_scraped_title": title,
            }

    state["sections"] = sections
    if global_newest and global_newest[0]:
        state["last_scraped_link"] = global_newest[0]
        state["last_scraped_title"] = global_newest[1]
    state["updated_at"] = datetime.now(timezone.utc).isoformat()

    os.makedirs(os.path.dirname(cp_path) or ".", exist_ok=True)
    with open(cp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def get_last_scraped_checkpoint(articles_json_path: str) -> tuple[str | None, str | None]:
    """
    Return (normalized_url, title) of the newest article from the previous run.
    That article is the stop boundary for the next incremental scrape.
    Returns (None, None) on first / bootstrap run.
    """
    cp_path = _checkpoint_path(articles_json_path)
    state = load_checkpoint_state(articles_json_path)
    if state.get("last_scraped_link"):
        try:
            link = state["last_scraped_link"]
            norm = normalize_link(link)
            title = state.get("last_scraped_title") or ""
            print(f"[INCREMENTAL] Checkpoint: {title[:70] or norm}")
            return norm, title
        except (KeyError, TypeError):
            pass
    if os.path.isfile(cp_path) and not state:
        try:
            with open(cp_path, encoding="utf-8") as f:
                state = json.load(f)
            link = state.get("last_scraped_link") or ""
            if link:
                norm = normalize_link(link)
                title = state.get("last_scraped_title") or ""
                print(f"[INCREMENTAL] Checkpoint: {title[:70] or norm}")
                return norm, title
        except (json.JSONDecodeError, OSError) as e:
            print(f"[INCREMENTAL] Could not read checkpoint file: {e}")

    # Fall back to first entry in the JSON archive
    articles = _load_articles_list(articles_json_path)
    if articles and isinstance(articles[0], dict) and articles[0].get("link"):
        link = normalize_link(articles[0]["link"])
        title = articles[0].get("title") or ""
        print(f"[INCREMENTAL] Checkpoint (from JSON[0]): {title[:70] or link}")
        return link, title

    print("[INCREMENTAL] No checkpoint found — first run (bootstrap)")
    return None, None


def is_last_scraped_article(link: str, checkpoint_link: str | None) -> bool:
    """True when this URL matches the last article scraped in the previous run."""
    if not checkpoint_link:
        return False
    return normalize_link(link) == checkpoint_link


def _save_checkpoint(articles_json_path: str, link: str, title: str = "") -> None:
    cp_path = _checkpoint_path(articles_json_path)
    os.makedirs(os.path.dirname(cp_path) or ".", exist_ok=True)
    state = load_checkpoint_state(articles_json_path)
    state["last_scraped_link"] = link
    state["last_scraped_title"] = title
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    with open(cp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def merge_and_save(
    json_path: str,
    new_articles: list[dict[str, Any]],
    *,
    max_articles: int = 2000,
) -> int:
    """
    Prepend new articles to the archive, dedupe by link, cap size.
    Saves a checkpoint pointing at the newest article (data[0]) so the
    next incremental run knows where to stop.
    Returns count of newly added rows.
    """
    os.makedirs(os.path.dirname(json_path) or ".", exist_ok=True)

    existing = _load_articles_list(json_path)
    seen = {normalize_link(a.get("link", "")) for a in new_articles if a.get("link")}
    merged = list(new_articles)
    added = len(new_articles)

    for article in existing:
        link = normalize_link(article.get("link", ""))
        if not link or link in seen:
            continue
        seen.add(link)
        merged.append(article)

    if len(merged) > max_articles:
        merged = merged[:max_articles]

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    # Update checkpoint to newest article
    if merged:
        newest = merged[0]
        _save_checkpoint(json_path, newest.get("link", ""), newest.get("title", ""))

    print(
        f"[INCREMENTAL] Saved {len(merged)} total articles "
        f"({added} new this run) -> {json_path}"
    )
    return added


def sync_global_checkpoint_from_sections(articles_json_path: str) -> bool:
    """Set global checkpoint to the newest section boundary (by article id or URL date)."""
    import re

    state = load_checkpoint_state(articles_json_path)
    sections = state.get("sections") or {}
    if not sections:
        return False

    best_link = ""
    best_title = ""
    best_article_id = 0
    for entry in sections.values():
        if not isinstance(entry, dict):
            continue
        link = entry.get("last_scraped_link") or ""
        id_match = re.search(r"/article/(\d+)", link)
        if id_match:
            aid = int(id_match.group(1))
            if aid > best_article_id:
                best_article_id = aid
                best_link = link
                best_title = entry.get("last_scraped_title") or ""

    if best_link:
        update_section_checkpoints(
            articles_json_path,
            {},
            global_newest=(best_link, best_title),
        )
        print(
            f"[INCREMENTAL] Global checkpoint synced from sections: "
            f"{best_title[:50] or best_link[:70]}"
        )
        return True

    best_key: tuple[str, str, str] = ("", "", "")
    for entry in sections.values():
        if not isinstance(entry, dict):
            continue
        link = entry.get("last_scraped_link") or ""
        match = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", link)
        if not match:
            continue
        key = match.groups()
        if key > best_key:
            best_key = key
            best_link = link
            best_title = entry.get("last_scraped_title") or ""

    if not best_link:
        for preferred in ("latest_news", "news", "local", "top-story"):
            entry = sections.get(preferred)
            if isinstance(entry, dict) and entry.get("last_scraped_link"):
                best_link = entry["last_scraped_link"]
                best_title = entry.get("last_scraped_title") or ""
                break

    if not best_link:
        return False

    update_section_checkpoints(
        articles_json_path,
        {},
        global_newest=(best_link, best_title),
    )
    print(
        f"[INCREMENTAL] Global checkpoint synced from sections: "
        f"{best_title[:50] or best_link[:70]}"
    )
    return True


def save_replace_only(
    json_path: str,
    articles: list[dict[str, Any]],
) -> int:
    """
    Overwrite the JSON with exactly these articles (use [] when none).
    Does not merge with previous file contents.
    Multi-section outlets sync global checkpoint from section boundaries.
    """
    os.makedirs(os.path.dirname(json_path) or ".", exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(articles, f, ensure_ascii=False, indent=2)

    state = load_checkpoint_state(json_path)
    multi_section = bool(state.get("sections"))

    if articles and not multi_section:
        newest = articles[0]
        _save_checkpoint(
            json_path,
            newest.get("link", ""),
            newest.get("title", ""),
        )
        print(
            f"[INCREMENTAL] Replaced file with {len(articles)} article(s) -> {json_path}"
        )
    elif articles and multi_section:
        print(
            f"[INCREMENTAL] Replaced file with {len(articles)} article(s) -> {json_path}"
        )
        sync_global_checkpoint_from_sections(json_path)
    else:
        print(
            f"[INCREMENTAL] No new articles — pipeline file cleared ([]) -> {json_path}"
        )
        if multi_section:
            sync_global_checkpoint_from_sections(json_path)

    return len(articles)


def load_known_links(json_path: str) -> set[str]:
    """All URLs in the archive. Used for within-run dedup."""
    known: set[str] = set()
    for item in _load_articles_list(json_path):
        if isinstance(item, dict) and item.get("link"):
            known.add(normalize_link(item["link"]))
    return known


def load_incremental_boundary_links(
    json_path: str,
    *,
    archive_json_path: str | None = None,
) -> tuple[str | None, set[str]]:
    """
    Normalized checkpoint URL plus every URL already stored (including checkpoint).
    Used on newest-first feeds so we stop instead of skipping with continue.

    archive_json_path: optional full-history file (delta output may be replace-only).
    """
    checkpoint_link, _ = get_last_scraped_checkpoint(json_path)
    known = load_known_links(json_path)
    if archive_json_path and os.path.normpath(archive_json_path) != os.path.normpath(
        json_path
    ):
        known |= load_known_links(archive_json_path)
    if checkpoint_link:
        known.add(checkpoint_link)
    return checkpoint_link, known


def should_stop_at_feed_item(
    link: str,
    *,
    checkpoint_link: str | None,
    known_previous: set[str],
) -> str | None:
    """
    Newest-first feed: return a stop reason when this item is already scraped.
    """
    norm = normalize_link(link)
    if checkpoint_link and norm == checkpoint_link:
        return "checkpoint"
    if norm in known_previous:
        return "known_previous"
    return None
