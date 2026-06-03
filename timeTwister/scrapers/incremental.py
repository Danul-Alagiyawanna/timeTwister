"""
Incremental scraping: stop when the *last scraped article* from the previous run
appears again on the feed (newest-first). New articles are prepended to the JSON file.

Stop logic:
  1. get_last_scraped_checkpoint() reads data[0] (the newest stored article) — that URL
     is the stop boundary for the next run.
  2. Scrapers walk the live feed top-to-bottom; call is_last_scraped_article() each step.
  3. When it returns True, halt — we've caught up.
  4. Safety cap if checkpoint never appears: 40 articles (bootstrap), 15 (normal run).
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
from typing import Any
from urllib.parse import urldefrag, urlparse

# Safety caps: stop even if checkpoint URL never appears on the feed
INCREMENTAL_BOOTSTRAP_LIMIT = 40  # first run (no checkpoint)
INCREMENTAL_RUN_LIMIT = 15  # normal runs (checkpoint exists but missing from lists)


def incremental_fetch_limit(*, bootstrap: bool) -> int:
    return INCREMENTAL_BOOTSTRAP_LIMIT if bootstrap else INCREMENTAL_RUN_LIMIT


def reached_incremental_limit(article_count: int, *, bootstrap: bool) -> bool:
    return article_count >= incremental_fetch_limit(bootstrap=bootstrap)


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


def get_last_scraped_checkpoint(articles_json_path: str) -> tuple[str | None, str | None]:
    """
    Return (normalized_url, title) of the newest article from the previous run.
    That article is the stop boundary for the next incremental scrape.
    Returns (None, None) on first / bootstrap run.
    """
    # Try dedicated checkpoint file first (written by merge_and_save)
    cp_path = _checkpoint_path(articles_json_path)
    if os.path.isfile(cp_path):
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
    state = {
        "last_scraped_link": link,
        "last_scraped_title": title,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
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
        f"({added} new this run) → {json_path}"
    )
    return added


def save_replace_only(
    json_path: str,
    articles: list[dict[str, Any]],
) -> int:
    """
    Overwrite the JSON with exactly these articles (use [] when none).
    Does not merge with previous file contents.
    Updates checkpoint to articles[0] when non-empty; leaves checkpoint unchanged when empty.
    """
    os.makedirs(os.path.dirname(json_path) or ".", exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(articles, f, ensure_ascii=False, indent=2)

    if articles:
        newest = articles[0]
        _save_checkpoint(
            json_path,
            newest.get("link", ""),
            newest.get("title", ""),
        )
        print(
            f"[INCREMENTAL] Replaced file with {len(articles)} article(s) → {json_path}"
        )
    else:
        print(f"[INCREMENTAL] No new articles — saved empty list → {json_path}")

    return len(articles)


def load_known_links(json_path: str) -> set[str]:
    """All URLs in the archive. Used for within-run dedup."""
    known: set[str] = set()
    for item in _load_articles_list(json_path):
        if isinstance(item, dict) and item.get("link"):
            known.add(normalize_link(item["link"]))
    return known
