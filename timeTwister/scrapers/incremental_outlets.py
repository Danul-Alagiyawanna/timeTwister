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
    collect_thinakaran_links,
    collect_virakesari_links,
    virakesari_article_id,
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


def _scrapling_html_driver(link: str, timeout: int = 25):
    from scrapling_fetch import fetch_text
    from scrapling_page import html_driver

    html = fetch_text(link, timeout=timeout)
    if html and len(html) > 500:
        return html_driver(html, link)
    return None


def _fetch_metadata(driver, link: str, mod: Any, use_timeout: bool = False) -> dict | None:
    page = _scrapling_html_driver(link)
    if page:
        if use_timeout:
            meta = mod.extract_with_timeout(page)
        else:
            meta = mod.extract_article_metadata(page)
        if meta and (meta.get("title") or meta.get("description")):
            return article_from_metadata(meta, link)

    driver.get(link)
    time.sleep(2)
    if use_timeout:
        meta = mod.extract_with_timeout(driver)
    else:
        meta = mod.extract_article_metadata(driver)
    if not meta:
        return None
    return article_from_metadata(meta, link)


def _fetch_content(
    driver,
    link: str,
    mod: Any,
    *,
    ensure_driver: Callable[[], Any] | None = None,
) -> dict | None:
    page = _scrapling_html_driver(link)
    if page:
        meta = mod.extract_with_timeout(page)
        if meta and (meta.get("title") or meta.get("description") or meta.get("summary")):
            return article_from_content(meta, link)

    if driver is None and ensure_driver is not None:
        driver = ensure_driver()
    if driver is None:
        return None

    driver.get(link)
    time.sleep(2)
    meta = mod.extract_with_timeout(driver)
    if not meta:
        return None
    return article_from_content(meta, link)


def _fetch_ceylontoday(driver, link: str, mod: Any) -> dict | None:
    from bs4 import BeautifulSoup

    html = None
    page = _scrapling_html_driver(link)
    if page:
        html = page.page_source
        soup = BeautifulSoup(html, "html.parser")
    else:
        driver.get(link)
        time.sleep(2)
        soup = BeautifulSoup(driver.page_source, "html.parser")

    title_el = soup.select_one("h1.entry-title") or soup.find("h1")
    title = title_el.get_text(strip=True) if title_el else ""
    date_tag = soup.find("time", class_="entry-date") or soup.find("meta", property="article:published_time")
    date_str = ""
    if date_tag:
        date_str = date_tag.get("datetime") or date_tag.get("content") or date_tag.get_text(strip=True)

    if html:
        desc, image_url = mod.get_enhanced_article_description(page, link, html=html)
    else:
        desc, image_url = mod.get_enhanced_article_description(driver, link)

    if not title and not desc:
        return None

    return {
        "title": title,
        "link": link,
        "summary": desc,
        "description": desc,
        "date": date_str or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "image_url": image_url if image_url not in ("N/A", "", "null") else "",
        "date_source": "Incremental scrape",
    }


def _collect_ceylontoday_links_scrapling(url: str) -> list[str]:
    from bs4 import BeautifulSoup
    from scrapling_fetch import fetch_text

    html = fetch_text(url, timeout=25)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    links: list[str] = []
    for article in soup.select("div.tdb_module_loop.td_module_wrap"):
        title_tag = article.select_one("h3.entry-title a")
        if title_tag and title_tag.get("href"):
            links.append(title_tag["href"])
    return links


def _collect_list_links_scrapling(url: str, mod: Any) -> list[str]:
    from scrapling_fetch import fetch_text
    from scrapling_page import html_driver

    html = fetch_text(url, timeout=25)
    if not html:
        return []
    return mod.get_main_article_links(html_driver(html, url))


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

    def collect(d, url):
        links = _collect_ceylontoday_links_scrapling(url)
        if links:
            return links
        return collect_ceylontoday_links(d, url)

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
    """economynext.com/feed/ (+ WP API fallback) via Scrapling HTTP fetcher."""
    import re as _re

    from bs4 import BeautifulSoup as BS

    from scrapling_fetch import fetch_bytes
    from incremental import (
        INCREMENTAL_BOOTSTRAP_LIMIT,
        INCREMENTAL_RUN_LIMIT,
        get_last_scraped_checkpoint,
        save_replace_only,
        _load_articles_list,
    )

    RSS_FEED = "https://economynext.com/feed/"
    WP_API = "https://economynext.com/wp-json/wp/v2/posts"
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

    print("[INCREMENTAL] EconomyNext — Scrapling RSS + WP API fallback")
    if bootstrap:
        print(f"[INCREMENTAL] No checkpoint; bootstrap max {max_articles} articles")
    else:
        print(f"[INCREMENTAL] Run safety cap: {max_articles} new articles")

    # --- source 1: economynext.com/feed/ ---
    articles_raw: list[dict] = []
    rss_body = fetch_bytes(
        RSS_FEED,
        accept="application/rss+xml, application/xml, */*",
        timeout=25,
        expect_xml=True,
    )
    if rss_body:
        rss_xml = rss_body.decode("utf-8", errors="replace")
        if not _is_cloudflare_block(rss_xml):
            parsed = _parse_economynext_rss_xml(rss_xml)
            if parsed:
                articles_raw = parsed
                print(
                    f"[INFO] economynext.com/feed/ OK via Scrapling, "
                    f"{len(articles_raw)} items"
                )
    if not articles_raw:
        print("[WARN] economynext.com/feed/ unavailable - trying WP API fallback")

    # --- source 2: WordPress REST API (fallback when RSS blocked) ---
    if not articles_raw:
        print("[INFO] RSS unavailable — trying WordPress REST API...")
        wp_body = fetch_bytes(
            WP_API,
            params={
                "per_page": 20,
                "_fields": "id,title,link,date,excerpt,content,jetpack_featured_media_url",
            },
            accept="application/json",
            timeout=25,
        )
        if wp_body:
            try:
                import json as _json
                posts = _json.loads(wp_body.decode("utf-8", errors="replace"))
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

    if not articles_raw:
        print("[ERROR] economynext.com/feed/ and WP API failed - saving empty list")
        save_replace_only(json_path, [])
        return 0

    for art in articles_raw:
        art["link"] = _canonical_economynext_link(
            art.get("link", ""), art.get("title", "")
        )
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
                f"[INCREMENTAL] Reached boundary ({stop}) - stopping.\n"
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
        links = _collect_list_links_scrapling(url, mod)
        if links:
            return links
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
    """
    Virakesari-only incremental loop (does not use shared per-section runner).
    Stops by numeric /article/NNNNN id; skips cross-section promo filter.
    """
    from incremental import (
        INCREMENTAL_BOOTSTRAP_LIMIT_PER_SECTION,
        INCREMENTAL_RUN_LIMIT_PER_SECTION,
        get_section_checkpoint,
        migrate_global_checkpoint_to_sections,
        save_replace_only,
        title_for_checkpoint_link,
        update_section_checkpoints,
    )
    from incremental_runner import data_json_path

    mod = _import_scraper("virakesari_selenium_json")
    pages = [
        (c, f"https://www.virakesari.lk/category/{c}")
        for c in ("local", "world", "sports", "feature", "business")
    ]
    json_path = data_json_path("virakesari_latest_news.json")
    section_keys = [name for name, _ in pages]
    migrate_global_checkpoint_to_sections(json_path, section_keys)
    bootstrap = not any(get_section_checkpoint(json_path, k)[0] for k in section_keys)
    max_articles = (
        INCREMENTAL_BOOTSTRAP_LIMIT_PER_SECTION
        if bootstrap
        else INCREMENTAL_RUN_LIMIT_PER_SECTION
    )

    print("[INCREMENTAL] Virakesari — numeric article-id checkpoints (isolated runner)")
    if bootstrap:
        print(f"[INCREMENTAL] No section checkpoint; bootstrap max {max_articles} per section")
    else:
        print(
            f"[INCREMENTAL] Run safety cap: {max_articles} new articles per section"
        )

    driver = None
    new_articles: list[dict[str, Any]] = []
    saved_ids: set[int] = set()
    section_links: dict[str, list[str]] = {}
    section_checkpoint_updates: dict[str, tuple[str, str]] = {}

    def _ensure_driver():
        nonlocal driver
        if driver is None:
            driver = mod.create_driver()
        return driver

    try:
        print("\n[INCREMENTAL] Phase 1 — list pages (Scrapling first)")
        for name, page_url in pages:
            print(f"\n{'=' * 50}\n[INCREMENTAL] Virakesari / {name}\n{page_url}")
            try:
                links = collect_virakesari_links(None, page_url)
                if not links:
                    links = collect_virakesari_links(_ensure_driver(), page_url)
                section_links[name] = links
                print(f"[INFO] {len(links)} links on list page")
            except Exception as e:
                print(f"[ERROR] List page failed ({name}): {e}")
                section_links[name] = []
            time.sleep(2.0)

        total_links = sum(len(v) for v in section_links.values())
        if total_links == 0:
            print(
                "[ERROR] All Virakesari list pages empty — "
                "keeping checkpoints and JSON unchanged"
            )
            return 0

        print("\n[INCREMENTAL] Phase 2 — fetch new articles per section (Scrapling first)")
        fetch_article = lambda d, l: _fetch_content(
            None, l, mod, ensure_driver=_ensure_driver
        )

        for name, _page_url in pages:
            links = section_links.get(name, [])
            if not links:
                print(f"  [SKIP] {name} — no list links, checkpoint unchanged")
                continue
            sec_ckpt, sec_ckpt_title = get_section_checkpoint(json_path, name)
            cp_id = virakesari_article_id(sec_ckpt or "") if sec_ckpt else 0
            print(
                f"\n[PHASE 2] {name} — checkpoint id {cp_id or 'none'}: "
                f"{(sec_ckpt or 'None')[:70]}"
            )

            if links and cp_id and virakesari_article_id(links[0]) == cp_id:
                print(
                    f"  [CAUGHT UP] {name} head id {cp_id} matches checkpoint — skip fetch"
                )
                section_checkpoint_updates[name] = (sec_ckpt, sec_ckpt_title)
                continue

            page_new = 0
            first_new_link: str | None = None
            hit_checkpoint = False

            for i, link in enumerate(links, 1):
                link_id = virakesari_article_id(link)
                if cp_id and link_id == cp_id:
                    print("  [STOP] Reached section checkpoint")
                    hit_checkpoint = True
                    break
                if cp_id and link_id and link_id < cp_id:
                    print("  [STOP] Passed checkpoint (older article on list)")
                    break
                if link_id in saved_ids:
                    continue

                print(f"\n[INFO] New {i} ({name}): id {link_id}...")
                try:
                    row = fetch_article(driver, link)
                    if row:
                        title = (row.get("title") or "").strip()
                        summary = (
                            (row.get("summary") or row.get("description") or "")
                        ).strip()
                        if not title and not summary:
                            print(f"[SKIP] Empty row (no title/body): {link[:80]}...")
                            continue
                        row["section"] = name
                        new_articles.append(row)
                        saved_ids.add(link_id)
                        page_new += 1
                        if first_new_link is None:
                            first_new_link = link
                except Exception as e:
                    print(f"[ERROR] Article failed: {e}")

                if page_new >= max_articles:
                    label = "Bootstrap" if bootstrap else "Run safety"
                    print(
                        f"[INCREMENTAL] {label} limit ({max_articles}) "
                        f"for {name} — next section"
                    )
                    break

                time.sleep(0.5)

            if page_new > 0 and first_new_link:
                section_checkpoint_updates[name] = (
                    first_new_link,
                    title_for_checkpoint_link(
                        first_new_link, new_articles, json_path
                    ),
                )
            elif hit_checkpoint and sec_ckpt:
                section_checkpoint_updates[name] = (sec_ckpt, sec_ckpt_title)

    finally:
        if driver is not None:
            driver.quit()

    new_articles = [
        a
        for a in new_articles
        if (a.get("title") or "").strip()
        or (a.get("summary") or a.get("description") or "").strip()
    ]
    print(f"\n[INCREMENTAL] New articles this run: {len(new_articles)}")

    if section_checkpoint_updates:
        global_link = ""
        global_title = ""
        global_id = 0
        for _name, (link, title) in section_checkpoint_updates.items():
            aid = virakesari_article_id(link)
            if aid > global_id:
                global_id = aid
                global_link = link
                global_title = title
        update_section_checkpoints(
            json_path,
            section_checkpoint_updates,
            global_newest=(global_link, global_title) if global_link else None,
        )
        for sec_name, (url, _) in section_checkpoint_updates.items():
            print(f"  [CKPT] {sec_name}: {url[:70]}")

    save_replace_only(json_path, new_articles)
    print("[INCREMENTAL] Virakesari finished.")
    return len(new_articles)


def run_thinakaran_incremental() -> int:
    """Thinakaran incremental — Scrapling StealthyFetcher on GHA; Selenium only locally."""
    from incremental import (
        INCREMENTAL_BOOTSTRAP_LIMIT_PER_SECTION,
        INCREMENTAL_RUN_LIMIT_PER_SECTION,
        get_section_checkpoint,
        migrate_global_checkpoint_to_sections,
        normalize_link,
        save_replace_only,
        sync_global_checkpoint_from_sections,
        title_for_checkpoint_link,
        update_section_checkpoints,
    )
    from incremental_runner import data_json_path

    mod = _import_scraper("thinakaran_selenium_json")
    pages = [
        (c, f"https://www.thinakaran.lk/category/{c}/")
        for c in mod.THINAKARAN_SECTIONS
    ]
    json_path = data_json_path("thinakaran_latest_news.json")
    section_keys = [name for name, _ in pages]
    migrate_global_checkpoint_to_sections(json_path, section_keys)
    bootstrap = not any(get_section_checkpoint(json_path, k)[0] for k in section_keys)
    max_articles = (
        INCREMENTAL_BOOTSTRAP_LIMIT_PER_SECTION
        if bootstrap
        else INCREMENTAL_RUN_LIMIT_PER_SECTION
    )

    is_ci = os.getenv("CI", "").lower() in ("1", "true", "yes")
    print(
        "[INCREMENTAL] Thinakaran — Scrapling"
        + (" StealthyFetcher on CI" if is_ci else " first, Selenium fallback locally")
    )
    if bootstrap:
        print(f"[INCREMENTAL] No section checkpoint; bootstrap max {max_articles} per section")
    else:
        print(f"[INCREMENTAL] Run safety cap: {max_articles} new articles per section")

    driver = None
    section_links: dict[str, list[str]] = {}
    section_rss_rows: dict[str, dict[str, dict]] = {}

    def _ensure_driver():
        nonlocal driver
        if driver is None:
            driver = mod.create_driver()
        return driver

    print("\n[INCREMENTAL] Phase 1 — list pages (Scrapling) + RSS fallback")
    for name, page_url in pages:
        print(f"\n{'=' * 50}\n[INCREMENTAL] Thinakaran / {name}\n{page_url}")
        links = collect_thinakaran_links(None, page_url)
        if not links and not is_ci:
            links = collect_thinakaran_links(_ensure_driver(), page_url)
        if not links:
            rss_items = mod.fetch_thinakaran_section_feed(name)
            links = [a["link"] for a in rss_items if a.get("link")]
            section_rss_rows[name] = {
                normalize_link(a["link"]): a for a in rss_items if a.get("link")
            }
            print(f"[PHASE 1] {name}: {len(links)} article(s) from RSS fallback")
        else:
            print(f"[INFO] {len(links)} links on list page")
        section_links[name] = links
        time.sleep(1.0)

    if sum(len(v) for v in section_links.values()) == 0:
        print("[ERROR] All Thinakaran sections empty — keeping data unchanged")
        if driver is not None:
            driver.quit()
        return 0

    new_articles: list[dict[str, Any]] = []
    saved_urls: set[str] = set()
    section_checkpoint_updates: dict[str, tuple[str, str]] = {}

    print("\n[INCREMENTAL] Phase 2 — new articles per section")
    fetch_article = lambda d, l: _fetch_content(
        None, l, mod, ensure_driver=_ensure_driver
    )

    for name, _page_url in pages:
        links = section_links.get(name, [])
        if not links:
            print(f"  [SKIP] {name} — no links, checkpoint unchanged")
            continue

        sec_ckpt, sec_ckpt_title = get_section_checkpoint(json_path, name)
        sec_ckpt_norm = normalize_link(sec_ckpt or "")
        print(f"\n[PHASE 2] {name} — checkpoint: {(sec_ckpt or 'None')[:70]}")

        if sec_ckpt_norm and links and normalize_link(links[0]) == sec_ckpt_norm:
            print(f"  [CAUGHT UP] {name} — head matches checkpoint")
            section_checkpoint_updates[name] = (sec_ckpt, sec_ckpt_title)
            continue

        page_new = 0
        first_new_link: str | None = None
        hit_checkpoint = False
        rss_by_url = section_rss_rows.get(name, {})

        for i, link in enumerate(links, 1):
            norm = normalize_link(link)
            if sec_ckpt_norm and norm == sec_ckpt_norm:
                print("  [STOP] Reached section checkpoint")
                hit_checkpoint = True
                break
            if norm in saved_urls:
                continue

            print(f"\n[INFO] New {i} ({name}): {link[:80]}...")
            try:
                rss_row = rss_by_url.get(norm)
                if rss_row:
                    summary = (
                        rss_row.get("summary") or rss_row.get("description") or ""
                    ).strip()
                    if len(summary) < 200:
                        row = fetch_article(driver, link)
                        if row:
                            rss_row = row
                    row = dict(rss_row)
                else:
                    row = fetch_article(driver, link)

                if row:
                    title = (row.get("title") or "").strip()
                    summary = (
                        (row.get("summary") or row.get("description") or "")
                    ).strip()
                    if not title and not summary:
                        print(f"[SKIP] Empty row: {link[:80]}...")
                        continue
                    row["section"] = name
                    new_articles.append(row)
                    saved_urls.add(norm)
                    page_new += 1
                    if first_new_link is None:
                        first_new_link = link
            except Exception as e:
                print(f"[ERROR] Article failed: {e}")

            if page_new >= max_articles:
                label = "Bootstrap" if bootstrap else "Run safety"
                print(
                    f"[INCREMENTAL] {label} limit ({max_articles}) "
                    f"for {name} — next section"
                )
                break

            time.sleep(0.3)

        if page_new > 0 and first_new_link:
            section_checkpoint_updates[name] = (
                first_new_link,
                title_for_checkpoint_link(first_new_link, new_articles, json_path),
            )
        elif hit_checkpoint and sec_ckpt:
            section_checkpoint_updates[name] = (sec_ckpt, sec_ckpt_title)

    if driver is not None:
        driver.quit()

    new_articles = [
        a
        for a in new_articles
        if (a.get("title") or "").strip()
        or (a.get("summary") or a.get("description") or "").strip()
    ]
    print(f"\n[INCREMENTAL] New articles this run: {len(new_articles)}")

    if section_checkpoint_updates:
        update_section_checkpoints(json_path, section_checkpoint_updates)
        sync_global_checkpoint_from_sections(json_path)
        for sec_name, (url, _) in section_checkpoint_updates.items():
            print(f"  [CKPT] {sec_name}: {url[:70]}")

    save_replace_only(json_path, new_articles)
    print("[INCREMENTAL] Thinakaran finished.")
    return len(new_articles)


def run_thamilan_incremental() -> int:
    from incremental import prune_alias_section_keys

    mod = _import_scraper("thamilan_selenium_json")
    pages = list(mod.CATEGORY_URLS)
    json_path = data_json_path("thamilan_latest_news.json")
    section_keys = [name for name, _ in pages]
    prune_alias_section_keys(json_path, section_keys)

    def fetch(d, link: str) -> dict | None:
        d.get(link)
        time.sleep(2)
        meta = mod.extract_article_content(d)
        if not meta:
            return None
        return article_from_content(meta, link)

    return run_incremental_scraper(
        outlet_name="Thamilan",
        data_filename="thamilan_latest_news.json",
        pages=pages,
        collect_links=collect_thamilan_links,
        fetch_article=fetch,
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
