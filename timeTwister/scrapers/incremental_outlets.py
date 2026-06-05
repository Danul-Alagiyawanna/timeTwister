"""
Wire all 13 remaining outlets to run_incremental_scraper (FT.lk-style).

Call from each scraper: run_incremental_for_module(__name__ split or basename)
"""
from __future__ import annotations

import importlib
import os
import sys
import time
from datetime import datetime
from typing import Any, Callable

# Ensure scrapers dir is on path when invoked as script
_SCRAPERS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRAPERS_DIR not in sys.path:
    sys.path.insert(0, _SCRAPERS_DIR)

from incremental_links import (
    collect_ceylontoday_links,
    collect_dailynews_links,
    collect_divaina_breaking_links,
    collect_divaina_main_links,
    collect_thamilan_links,
    collect_virakesari_links,
    collect_wp_category_links,
)
from incremental_runner import (
    article_from_content,
    article_from_metadata,
    data_json_path,
    run_incremental_scraper,
)

CollectFn = Callable[[Any, str], list[str]]


def _import_scraper(module_name: str):
    return importlib.import_module(module_name)


def _fetch_metadata(driver, link: str, mod: Any, use_timeout: bool = False) -> dict | None:
    driver.get(link)
    time.sleep(2)
    if use_timeout:
        meta = mod.extract_with_timeout(driver)
    else:
        meta = mod.extract_article_metadata(driver)
    if not meta:
        return None
    return article_from_metadata(meta, link)


def _fetch_content(driver, link: str, mod: Any) -> dict | None:
    driver.get(link)
    time.sleep(2)
    meta = mod.extract_with_timeout(driver)
    if not meta:
        return None
    return article_from_content(meta, link)


def _fetch_ceylontoday(driver, link: str, mod: Any) -> dict | None:
    from bs4 import BeautifulSoup

    driver.get(link)
    time.sleep(2)
    soup = BeautifulSoup(driver.page_source, "html.parser")
    title_el = soup.select_one("h1.entry-title") or soup.find("h1")
    title = title_el.get_text(strip=True) if title_el else ""
    date_tag = soup.find("time", class_="entry-date") or soup.find("meta", property="article:published_time")
    date_str = ""
    if date_tag:
        date_str = date_tag.get("datetime") or date_tag.get("content") or date_tag.get_text(strip=True)
    desc, image_url = mod.get_enhanced_article_description(driver, link)
    return {
        "title": title,
        "link": link,
        "summary": desc,
        "description": desc,
        "date": date_str or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "image_url": image_url if image_url not in ("N/A", "", "null") else "",
        "date_source": "Incremental scrape",
    }


# --- per-outlet runners ---

def run_dailynews_incremental() -> int:
    mod = _import_scraper("dailynews_selenium_json")
    cats = ["local", "politics", "business", "lawnorder", "world", "sports"]
    pages = [(c, f"https://dailynews.lk/category/{c}/") for c in cats]

    def fetch(d, link):
        return _fetch_metadata(d, link, mod)

    return run_incremental_scraper(
        outlet_name="Daily News",
        data_filename="dailynews_latest_news.json",
        pages=pages,
        collect_links=collect_dailynews_links,
        fetch_article=fetch,
        use_undetected=True,
    )


def run_ceylontoday_incremental() -> int:
    mod = _import_scraper("ceylontoday_selenium_json")
    cats = ["news", "columns", "features", "sports", "world", "business"]
    pages = [
        (c, f"https://ceylontoday.lk/category/ceylon-today-daily/{c}/") for c in cats
    ]

    def fetch(d, link):
        return _fetch_ceylontoday(d, link, mod)

    return run_incremental_scraper(
        outlet_name="Ceylon Today",
        data_filename="ceylontoday_finance.json",
        pages=pages,
        collect_links=collect_ceylontoday_links,
        fetch_article=fetch,
        create_driver=mod.setup_driver,
        use_undetected=False,
    )


def run_dailymirror_incremental() -> int:
    return _import_scraper("dailymirror_selenium_json").main_incremental()


# --- EconomyNext incremental helpers (GNews redirect URLs break URL-only checkpoints) ---

_EN_TITLE_SUFFIX_RE = __import__("re").compile(r"\s*[-|]\s*EconomyNext\s*$", __import__("re").I)


def _en_normalize_title(title: str) -> str:
    return _EN_TITLE_SUFFIX_RE.sub("", (title or "").strip()).lower()


def _decode_gnews_url_en(gnews_url: str) -> str:
    """Resolve Google News redirect to economynext.com when possible."""
    import base64 as _b64
    import re as _re

    if not gnews_url or "news.google.com" not in gnews_url:
        return gnews_url
    try:
        from googlenewsdecoder import gnewsdecoder  # type: ignore

        result = gnewsdecoder(gnews_url)
        decoded = (result or {}).get("decoded_url") if isinstance(result, dict) else None
        if decoded and "economynext.com" in decoded:
            return decoded
    except Exception:
        pass
    m = _re.search(r"/articles/([^?&#]+)", gnews_url)
    if not m:
        return gnews_url
    try:
        padded = m.group(1) + "=" * (-len(m.group(1)) % 4)
        raw = _b64.urlsafe_b64decode(padded)
        for pat in (
            rb"https?://(?:www\.)?economynext\.com/[^\x00-\x20\"'<>]+",
        ):
            fm = _re.search(pat, raw)
            if fm:
                return fm.group(0).decode("utf-8", errors="ignore").rstrip(".")
    except Exception:
        pass
    return gnews_url


def _canonical_economynext_link(url: str, title: str = "") -> str:
    from incremental import normalize_link

    if not url:
        return ""
    if "news.google.com" in url:
        url = _decode_gnews_url_en(url)
    if "economynext.com" in url:
        return normalize_link(url)
    return normalize_link(url)


def _en_article_key(link: str, title: str) -> str:
    from incremental import normalize_link

    if link and "economynext.com" in link:
        return "url:" + normalize_link(link)
    title_key = _en_normalize_title(title)
    if title_key:
        return "title:" + title_key
    return "url:" + normalize_link(link)


def _en_boundary_stop_reason(
    article: dict[str, Any],
    checkpoint_link: str | None,
    checkpoint_title: str | None,
    known_keys: set[str],
) -> str | None:
    link = article.get("link", "")
    title = article.get("title", "")
    key = _en_article_key(link, title)

    if checkpoint_link:
        cp_key = _en_article_key(checkpoint_link, checkpoint_title or "")
        if key == cp_key:
            return "checkpoint"
    if checkpoint_title and _en_normalize_title(title) == _en_normalize_title(
        checkpoint_title
    ):
        return "checkpoint_title"
    if key in known_keys:
        return "known_previous"
    return None


def _is_cloudflare_block(html: str) -> bool:
    if not html or len(html) < 200:
        return True
    markers = (
        "cf-browser-verification",
        "Just a moment",
        "Attention Required! | Cloudflare",
        "Enable JavaScript and cookies",
    )
    lower = html[:8000].lower()
    return any(m.lower() in lower for m in markers)


def _parse_economynext_rss_xml(
    xml_text: str,
    *,
    date_source_prefix: str = "RSS",
) -> list[dict[str, Any]]:
    """Parse economynext.com/feed/ (or equivalent XML) into article dicts."""
    import re as _re
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime

    from bs4 import BeautifulSoup as BS

    if not xml_text or _is_cloudflare_block(xml_text):
        return []
    ns = {"content": "http://purl.org/rss/1.0/modules/content/"}
    articles: list[dict[str, Any]] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    for item in root.findall(".//item"):
        link = (item.findtext("link") or "").strip()
        if not link or "economynext.com" not in link:
            continue
        title = (item.findtext("title") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        description_html = (item.findtext("description") or "").strip()
        content_html = (
            item.findtext("content:encoded", namespaces=ns) or ""
        ).strip()
        date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if pub_date:
            try:
                date_str = parsedate_to_datetime(pub_date).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            except Exception:
                pass
        desc_text = ""
        if description_html:
            try:
                desc_text = BS(description_html, "html.parser").get_text(
                    separator="\n", strip=True
                )
            except Exception:
                desc_text = description_html
        image_url = ""
        if content_html:
            m = _re.search(r'<img[^>]+src=["\']([^"\']+)["\']', content_html)
            if m:
                image_url = m.group(1)
        articles.append(
            {
                "title": title,
                "link": link,
                "summary": desc_text,
                "description": desc_text,
                "date": date_str,
                "image_url": image_url,
                "date_source": f"{date_source_prefix}: {date_str}",
            }
        )
    return articles


def _en_rss_lookup(articles: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index RSS rows by canonical URL and normalized title."""
    by_key: dict[str, dict[str, Any]] = {}
    for art in articles:
        link = _canonical_economynext_link(art.get("link", ""), art.get("title", ""))
        art = {**art, "link": link}
        by_key[_en_article_key(link, art.get("title", ""))] = art
        title_key = _en_normalize_title(art.get("title", ""))
        if title_key:
            by_key.setdefault("title:" + title_key, art)
    return by_key


def _apply_rss_basis(
    articles: list[dict[str, Any]],
    rss_lookup: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Replace fallback-source rows with economynext.com/feed/ data when matched."""
    if not rss_lookup:
        return articles
    merged: list[dict[str, Any]] = []
    for art in articles:
        link = _canonical_economynext_link(art.get("link", ""), art.get("title", ""))
        key = _en_article_key(link, art.get("title", ""))
        title_key = "title:" + _en_normalize_title(art.get("title", ""))
        basis = rss_lookup.get(key) or rss_lookup.get(title_key)
        merged.append(basis if basis else {**art, "link": link})
    return merged


def _repair_economynext_checkpoint(json_path: str) -> None:
    """Rewrite legacy GNews checkpoint URLs to canonical economynext.com links."""
    import json as _json
    from datetime import timezone

    from incremental import load_checkpoint_state, normalize_link

    state = load_checkpoint_state(json_path)
    link = (state.get("last_scraped_link") or "").strip()
    title = state.get("last_scraped_title") or ""
    if not link or "news.google.com" not in link:
        return
    canonical = _canonical_economynext_link(link, title)
    if not canonical or canonical == normalize_link(link):
        return
    if "economynext.com" not in canonical:
        return
    base = os.path.splitext(os.path.basename(json_path))[0]
    directory = os.path.dirname(json_path) or "."
    cp_path = os.path.join(directory, f"{base}_checkpoint.json")
    state["last_scraped_link"] = canonical
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    os.makedirs(directory, exist_ok=True)
    with open(cp_path, "w", encoding="utf-8") as f:
        _json.dump(state, f, ensure_ascii=False, indent=2)
    print(f"[INCREMENTAL] Migrated GNews checkpoint -> {canonical}")


def run_economynext_incremental() -> int:
    """Pure RSS approach — no Selenium, no Cloudflare issues from GHA."""
    import re as _re
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime

    from bs4 import BeautifulSoup as BS

    from incremental import (
        INCREMENTAL_BOOTSTRAP_LIMIT,
        INCREMENTAL_RUN_LIMIT,
        get_last_scraped_checkpoint,
        save_replace_only,
        _load_articles_list,
    )

    RSS_FEED = "https://economynext.com/feed/"
    WP_API = "https://economynext.com/wp-json/wp/v2/posts"
    GNEWS_RSS = "https://news.google.com/rss/search?q=site:economynext.com&hl=en-US&gl=US&ceid=US:en"
    json_path = data_json_path("economynext_latest_news.json")

    _repair_economynext_checkpoint(json_path)

    checkpoint_link, checkpoint_title = get_last_scraped_checkpoint(json_path)
    if checkpoint_link:
        checkpoint_link = _canonical_economynext_link(
            checkpoint_link, checkpoint_title or ""
        )
    bootstrap = not checkpoint_link and not checkpoint_title
    max_articles = INCREMENTAL_BOOTSTRAP_LIMIT if bootstrap else INCREMENTAL_RUN_LIMIT

    known_keys: set[str] = set()
    for item in _load_articles_list(json_path):
        if not isinstance(item, dict):
            continue
        link = _canonical_economynext_link(
            item.get("link", ""), item.get("title", "")
        )
        known_keys.add(_en_article_key(link, item.get("title", "")))
    if checkpoint_link or checkpoint_title:
        known_keys.add(
            _en_article_key(checkpoint_link or "", checkpoint_title or "")
        )
    if known_keys:
        print(f"[INCREMENTAL] Boundary keys from previous run: {len(known_keys)}")

    print("[INCREMENTAL] EconomyNext — RSS/WP-API (no Selenium/Cloudflare)")
    if bootstrap:
        print(f"[INCREMENTAL] No checkpoint; bootstrap max {max_articles} articles")
    else:
        print(f"[INCREMENTAL] Run safety cap: {max_articles} new articles")

    _BROWSER_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }

    def _get(url: str, **kw) -> "requests.Response | None":  # type: ignore[name-defined]
        """curl_cffi first (GHA Cloudflare), then requests."""
        for profile in ("chrome124", "safari17_0", "firefox133"):
            try:
                from curl_cffi import requests as cf_req  # type: ignore

                r = cf_req.get(
                    url,
                    impersonate=profile,
                    timeout=15,
                    headers=_BROWSER_HEADERS,
                    **kw,
                )
                if r.status_code == 200:
                    print(f"[INFO] curl_cffi ({profile}) got 200 for {url}")
                    return r
                print(f"[WARN] curl_cffi ({profile}) got {r.status_code}")
            except ImportError:
                break
            except Exception as e:
                print(f"[WARN] curl_cffi ({profile}) failed: {e}")
        import requests as _req

        try:
            r = _req.get(
                url,
                timeout=15,
                allow_redirects=True,
                headers=_BROWSER_HEADERS,
                **kw,
            )
            if r.status_code == 200:
                return r
            print(f"[WARN] requests {r.status_code} for {url}")
        except Exception as e:
            print(f"[WARN] requests failed: {e}")
        return None

    # --- source 1: economynext.com/feed/ (authoritative; retry every TLS profile) ---
    articles_raw: list[dict] = []
    rss_basis: list[dict] = []
    for attempt, profile in enumerate(
        ("chrome124", "safari17_0", "firefox133", "chrome124"), start=1
    ):
        try:
            from curl_cffi import requests as cf_req  # type: ignore

            resp = cf_req.get(
                RSS_FEED,
                impersonate=profile,
                timeout=20,
                headers={**_BROWSER_HEADERS, "Accept": "application/rss+xml, application/xml, */*"},
            )
            if resp.status_code == 200 and not _is_cloudflare_block(resp.text):
                rss_basis = _parse_economynext_rss_xml(resp.text)
                if rss_basis:
                    articles_raw = rss_basis
                    print(
                        f"[INFO] economynext.com/feed/ OK via curl_cffi ({profile}), "
                        f"{len(articles_raw)} items (attempt {attempt})"
                    )
                    break
                print(f"[WARN] RSS XML empty/invalid from curl_cffi ({profile})")
            else:
                print(
                    f"[WARN] RSS blocked or bad status from curl_cffi ({profile}): "
                    f"{getattr(resp, 'status_code', '?')}"
                )
        except ImportError:
            break
        except Exception as e:
            print(f"[WARN] RSS curl_cffi ({profile}) attempt {attempt}: {e}")

    if not articles_raw:
        resp = _get(RSS_FEED)
        if resp and not _is_cloudflare_block(resp.text):
            rss_basis = _parse_economynext_rss_xml(resp.text)
            if rss_basis:
                articles_raw = rss_basis
                print(f"[INFO] economynext.com/feed/ OK via requests, {len(articles_raw)} items")
    if articles_raw:
        rss_basis = list(articles_raw)
    else:
        print("[WARN] economynext.com/feed/ unavailable — trying fallbacks")

    # --- source 2: WordPress REST API (fallback when RSS blocked) ---
    if not articles_raw:
        print("[INFO] RSS unavailable — trying WordPress REST API...")
        resp = _get(WP_API, params={
            "per_page": 20,
            "_fields": "id,title,link,date,excerpt,content,jetpack_featured_media_url",
        })
        if resp:
            try:
                import json as _json
                posts = _json.loads(resp.text)
                for post in posts:
                    link = post.get("link", "").strip()
                    if not link:
                        continue
                    title = BS(post.get("title", {}).get("rendered", ""), "html.parser").get_text(strip=True)
                    date_str = post.get("date", "")
                    if date_str:
                        # WP returns ISO 8601: 2026-06-03T12:21:24
                        try:
                            date_str = datetime.fromisoformat(date_str).strftime("%Y-%m-%d %H:%M:%S")
                        except Exception:
                            pass
                    else:
                        date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    excerpt_html = post.get("excerpt", {}).get("rendered", "")
                    desc_text = BS(excerpt_html, "html.parser").get_text(separator="\n", strip=True) if excerpt_html else ""
                    image_url = post.get("jetpack_featured_media_url", "") or ""
                    if not image_url:
                        content_html = post.get("content", {}).get("rendered", "")
                        m = _re.search(r'<img[^>]+src=["\']([^"\']+)["\']', content_html)
                        if m:
                            image_url = m.group(1)
                    articles_raw.append({"title": title, "link": link, "summary": desc_text,
                                         "description": desc_text, "date": date_str,
                                         "image_url": image_url, "date_source": f"WP-API: {date_str}"})
                print(f"[INFO] WP-API returned {len(articles_raw)} posts")
            except Exception as e:
                print(f"[WARN] WP-API parse error: {e}")

    # --- source 3: Google News (discovery only; overlay economynext.com/feed/ when possible) ---
    if not articles_raw:
        print("[INFO] WP-API unavailable — trying Google News RSS (discovery only)...")

        resp = _get(GNEWS_RSS)
        if resp and len(resp.text) > 200:
            try:
                root = ET.fromstring(resp.text)
                for item in root.findall(".//item"):
                    gnews_link = (item.findtext("link") or "").strip()
                    if not gnews_link:
                        continue
                    title = (item.findtext("title") or "").strip()
                    link = _canonical_economynext_link(gnews_link, title)
                    if "economynext.com" not in link:
                        link = gnews_link
                    pub_date = (item.findtext("pubDate") or "").strip()
                    desc_html = (item.findtext("description") or "").strip()
                    date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    if pub_date:
                        try:
                            from email.utils import parsedate_to_datetime as _p2d
                            date_str = _p2d(pub_date).strftime("%Y-%m-%d %H:%M:%S")
                        except Exception:
                            pass
                    desc_text = BS(desc_html, "html.parser").get_text(separator="\n", strip=True) if desc_html else ""
                    articles_raw.append({"title": title, "link": link, "summary": desc_text,
                                         "description": desc_text, "date": date_str,
                                         "image_url": "", "date_source": f"GNews: {date_str}"})
                print(f"[INFO] Google News RSS returned {len(articles_raw)} items")
            except ET.ParseError as e:
                print(f"[WARN] Google News RSS parse error: {e}")

        # Last attempt: fetch native RSS again and replace GNews rows with feed data
        if articles_raw and not rss_basis:
            resp = _get(RSS_FEED)
            if resp and not _is_cloudflare_block(resp.text):
                rss_basis = _parse_economynext_rss_xml(resp.text)
                if rss_basis:
                    print(
                        f"[INFO] economynext.com/feed/ recovered for basis merge "
                        f"({len(rss_basis)} items)"
                    )
        if articles_raw and rss_basis:
            before = sum(1 for a in articles_raw if a.get("date_source", "").startswith("GNews"))
            articles_raw = _apply_rss_basis(articles_raw, _en_rss_lookup(rss_basis))
            after = sum(1 for a in articles_raw if a.get("date_source", "").startswith("RSS"))
            print(
                f"[INFO] Merged GNews discovery with economynext.com/feed/ "
                f"({after} RSS rows, was {before} GNews)"
            )

    if not articles_raw:
        print("[ERROR] All sources failed — saving empty list")
        save_replace_only(json_path, [])
        return 0

    # Canonical links + newest-first (GNews feed order is not reliable)
    for art in articles_raw:
        raw_link = art.get("link", "")
        art["link"] = _canonical_economynext_link(raw_link, art.get("title", ""))
        if "news.google.com" in raw_link and "economynext.com" in art["link"]:
            ds = art.get("date_source", "")
            if ds.startswith("GNews:"):
                art["date_source"] = ds.replace("GNews:", "GNews-EN:", 1)
    articles_raw.sort(key=lambda a: a.get("date", ""), reverse=True)

    # --- apply checkpoint / dedup / cap (newest-first: stop at boundary, don't skip) ---
    new_articles: list[dict] = []
    seen_this_run: set[str] = set()

    for art in articles_raw:
        stop = _en_boundary_stop_reason(
            art, checkpoint_link, checkpoint_title, known_keys
        )
        if stop:
            print(
                f"[INCREMENTAL] Reached boundary ({stop}) — stopping.\n"
                f"             {art.get('title', '')[:70]}"
            )
            break

        key = _en_article_key(art.get("link", ""), art.get("title", ""))
        if key in seen_this_run:
            continue

        if not art.get("title") and not art.get("summary"):
            print(f"[SKIP] Empty row: {art.get('link', '')[:80]}")
            continue

        new_articles.append(art)
        seen_this_run.add(key)
        print(f"[INFO] +Article: {art['title'][:70]}")

        if len(new_articles) >= max_articles:
            label = "Bootstrap" if bootstrap else "Run safety"
            print(f"[INCREMENTAL] {label} limit ({max_articles}) reached.")
            break

    print(f"\n[INCREMENTAL] New articles this run: {len(new_articles)}")
    save_replace_only(json_path, new_articles)
    print("[INCREMENTAL] EconomyNext finished.")
    return len(new_articles)


def run_themorning_incremental() -> int:
    """Per-section checkpoints — each category tracks its own last-scraped URL."""
    from incremental import (
        get_section_checkpoint,
        incremental_fetch_limit,
        load_known_links,
        normalize_link,
        reached_section_incremental_limit,
        save_replace_only,
        apply_section_head_checkpoints,
        migrate_global_checkpoint_to_sections,
    )
    from incremental_runner import create_standard_driver

    mod = _import_scraper("themorning_selenium_json")
    cats = ["news", "opinion", "business", "features", "sports", "world"]
    pages = [(c, f"https://www.themorning.lk/categories/{c}") for c in cats]
    json_path = data_json_path("themorning_latest_news.json")

    # bootstrap = no section has a checkpoint yet
    migrate_global_checkpoint_to_sections(json_path, cats)
    bootstrap = not any(get_section_checkpoint(json_path, c)[0] for c in cats)
    max_per_section = incremental_fetch_limit(bootstrap=bootstrap, per_section=True)
    known_previous = load_known_links(json_path)
    if known_previous:
        print(f"[INCREMENTAL] Skipping {len(known_previous)} URL(s) from previous file")

    print("[INCREMENTAL] The Morning — per-section checkpoints")
    if bootstrap:
        print(f"[INCREMENTAL] No checkpoint; bootstrap max {max_per_section} per section")
    else:
        print(f"[INCREMENTAL] Run safety cap: {max_per_section} new articles per section")

    driver = create_standard_driver(use_undetected=False)

    # Phase 1: collect links only (no seeding yet — seeding before Phase 2 causes
    # Phase 2 to stop immediately on the seeded article)
    section_links: dict[str, list[str]] = {}
    for cat, url in pages:
        print(f"\n[PHASE 1] {cat}: {url}")
        try:
            driver.get(url)
            time.sleep(3)
            links = mod.get_main_article_links(driver)
            section_links[cat] = links
            print(f"  {len(links)} links")
        except Exception as e:
            print(f"  [ERROR] {e}")
            section_links[cat] = []

    # Phase 2: fetch new articles per section (cap per category)
    new_articles: list[dict] = []
    seen_this_run: set[str] = set()

    for cat, _url in pages:
        section_new = 0
        links = section_links.get(cat, [])
        sec_ckpt, _ = get_section_checkpoint(json_path, cat)
        print(f"\n[PHASE 2] {cat} — checkpoint: {(sec_ckpt or 'None')[:70]}")

        for link in links:
            norm = normalize_link(link)
            if sec_ckpt and normalize_link(sec_ckpt) == norm:
                print(f"  [STOP] Reached section checkpoint")
                break
            # replace-only: previous file = last batch; feed is newest-first —
            # hitting a known URL means everything below is already saved → stop section
            if norm in known_previous:
                print(f"  [STOP] Already in previous run: {link[:70]}")
                break
            if norm in seen_this_run:
                continue
            try:
                meta = _fetch_metadata(driver, link, mod)
                if meta and (meta.get("title") or meta.get("summary")):
                    new_articles.append(meta)
                    seen_this_run.add(norm)
                    section_new += 1
                    print(f"  [+] {meta.get('title', '')[:70]}")
            except Exception as e:
                print(f"  [ERROR] {e}")

            if reached_section_incremental_limit(section_new, bootstrap=bootstrap):
                label = "Bootstrap" if bootstrap else "Run safety"
                print(
                    f"[INCREMENTAL] {label} limit ({max_per_section}) "
                    f"for section {cat} — next section"
                )
                break

            time.sleep(0.5)

    apply_section_head_checkpoints(
        json_path,
        section_links,
        new_articles,
        section_keys=cats,
    )

    driver.quit()

    print(f"\n[INCREMENTAL] New articles this run: {len(new_articles)}")
    save_replace_only(json_path, new_articles)
    print("[INCREMENTAL] The Morning finished.")
    return len(new_articles)


def run_sundayobserver_incremental() -> int:
    mod = _import_scraper("sundayobserver_selenium_json")
    pages = [
        ("news", "https://www.sundayobserver.lk/category/news/"),
        ("business", "https://www.sundayobserver.lk/category/business/"),
        ("sports", "https://www.sundayobserver.lk/category/sports/"),
    ]

    def collect(d, url):
        d.get(url)
        time.sleep(3)
        return mod.get_main_article_links(d)

    def fetch(d, link):
        return _fetch_metadata(d, link, mod)

    return run_incremental_scraper(
        outlet_name="Sunday Observer",
        data_filename="sundayobserver_latest_news.json",
        pages=pages,
        collect_links=collect,
        fetch_article=fetch,
        use_undetected=True,
    )


def run_dinamina_incremental() -> int:
    return _import_scraper("dinamina_selenium_json").main_incremental()


def run_divaina_incremental() -> int:
    return _import_scraper("divaina_selenium_json").main_incremental()


def run_lankadeepa_incremental() -> int:
    return _import_scraper("lankadeepa_selenium_json").main_incremental()


def run_mawbima_incremental() -> int:
    mod = _import_scraper("mawbima_selenium_json")
    pages = [
        ("local", "https://mawbima.lk/category/%e0%b6%af%e0%b7%9a%e0%b7%81%e0%b7%93%e0%b6%ba/"),
        ("foreign", "https://mawbima.lk/category/%e0%b7%80%e0%b7%92%e0%b6%af%e0%b7%9a%e0%b7%81%e0%b7%93%e0%b6%ba/"),
        ("sports", "https://mawbima.lk/category/%e0%b6%9a%e0%b7%8a%e0%b6%bb%e0%b7%93%e0%b6%a9%e0%b7%8f/"),
        ("business", "https://mawbima.lk/category/%e0%b7%80%e0%b7%8a%e0%b6%ba%e0%b7%8f%e0%b6%b4%e0%b7%8f%e0%b6%bb%e0%b7%92%e0%b6%9a/"),
    ]

    def collect(d, url):
        d.get(url)
        time.sleep(3)
        return mod.get_main_article_links(d)

    return run_incremental_scraper(
        outlet_name="Mawbima",
        data_filename="mawbima_latest_news.json",
        pages=pages,
        collect_links=collect,
        fetch_article=lambda d, l: _fetch_metadata(d, l, mod),
        use_undetected=True,
    )


def run_virakesari_incremental() -> int:
    mod = _import_scraper("virakesari_selenium_json")
    cats = ["local", "world", "sports", "feature", "business"]
    pages = [(c, f"https://www.virakesari.lk/category/{c}") for c in cats]

    return run_incremental_scraper(
        outlet_name="Virakesari",
        data_filename="virakesari_latest_news.json",
        pages=pages,
        collect_links=collect_virakesari_links,
        fetch_article=lambda d, l: _fetch_content(d, l, mod),
        use_undetected=True,
    )


def run_thinakaran_incremental() -> int:
    mod = _import_scraper("thinakaran_selenium_json")
    cats = ["local", "politics", "features", "editorial", "sports", "business"]
    pages = [(c, f"https://www.thinakaran.lk/category/{c}/") for c in cats]
    collect = lambda d, u: collect_wp_category_links(d, u, "thinakaran")

    return run_incremental_scraper(
        outlet_name="Thinakaran",
        data_filename="thinakaran_latest_news.json",
        pages=pages,
        collect_links=collect,
        fetch_article=lambda d, l: _fetch_content(d, l, mod),
        use_undetected=True,
    )


def _fetch_thamilan(driver, link: str, mod: Any) -> dict | None:
    driver.get(link)
    time.sleep(2)
    meta = mod.extract_article_content(driver)
    if not meta:
        return None
    return article_from_content(meta, link)


def run_thamilan_incremental() -> int:
    mod = _import_scraper("thamilan_selenium_json")
    pages = list(mod.CATEGORY_URLS)

    return run_incremental_scraper(
        outlet_name="Thamilan",
        data_filename="thamilan_latest_news.json",
        pages=pages,
        collect_links=collect_thamilan_links,
        fetch_article=lambda d, l: _fetch_thamilan(d, l, mod),
        use_undetected=True,
    )


INCREMENTAL_BY_MODULE: dict[str, Callable[[], int]] = {
    "dailynews_selenium_json": run_dailynews_incremental,
    "ceylontoday_selenium_json": run_ceylontoday_incremental,
    "dailymirror_selenium_json": run_dailymirror_incremental,
    "economynext_selenium_json": run_economynext_incremental,
    "themorning_selenium_json": run_themorning_incremental,
    "sundayobserver_selenium_json": run_sundayobserver_incremental,
    "dinamina_selenium_json": run_dinamina_incremental,
    "divaina_selenium_json": run_divaina_incremental,
    "lankadeepa_selenium_json": run_lankadeepa_incremental,
    "mawbima_selenium_json": run_mawbima_incremental,
    "virakesari_selenium_json": run_virakesari_incremental,
    "thinakaran_selenium_json": run_thinakaran_incremental,
    "thamilan_selenium_json": run_thamilan_incremental,
}


def run_incremental_for_module(module_name: str) -> int:
    """module_name: e.g. 'dailynews_selenium_json' (file basename without .py)."""
    base = module_name.replace(".py", "").split(".")[-1]
    fn = INCREMENTAL_BY_MODULE.get(base)
    if not fn:
        raise ValueError(f"No incremental runner for module: {module_name}")
    return fn()
