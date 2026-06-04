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
    collect_dailymirror_links,
    collect_dailynews_links,
    collect_divaina_breaking_links,
    collect_divaina_main_links,
    collect_lankadeepa_links,
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
    mod = _import_scraper("dailymirror_selenium_json")
    pages = [("latest", mod.BASE_URL)]

    def fetch(d, link):
        return _fetch_content(d, link, mod)

    return run_incremental_scraper(
        outlet_name="Daily Mirror",
        data_filename="dailymirror_latest_news.json",
        pages=pages,
        collect_links=collect_dailymirror_links,
        fetch_article=fetch,
        use_undetected=True,
    )


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
        is_last_scraped_article,
        load_known_links,
        normalize_link,
        save_replace_only,
    )

    RSS_FEED = "https://economynext.com/feed/"
    WP_API = "https://economynext.com/wp-json/wp/v2/posts"
    GNEWS_RSS = "https://news.google.com/rss/search?q=site:economynext.com&hl=en-US&gl=US&ceid=US:en"
    json_path = data_json_path("economynext_latest_news.json")

    checkpoint_link, _ = get_last_scraped_checkpoint(json_path)
    bootstrap = not checkpoint_link
    max_articles = INCREMENTAL_BOOTSTRAP_LIMIT if bootstrap else INCREMENTAL_RUN_LIMIT
    known_previous = load_known_links(json_path)
    if known_previous:
        print(f"[INCREMENTAL] Skipping {len(known_previous)} URL(s) from previous file")

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
        import requests as _req
        try:
            r = _req.get(url, timeout=15, allow_redirects=True, headers=_BROWSER_HEADERS, **kw)
            if r.status_code == 200:
                return r
            print(f"[WARN] requests {r.status_code} for {url}")
        except Exception as e:
            print(f"[WARN] requests failed: {e}")
        # curl_cffi with multiple profiles
        for profile in ("chrome124", "safari17_0", "firefox133"):
            try:
                from curl_cffi import requests as cf_req  # type: ignore
                r = cf_req.get(url, impersonate=profile, timeout=15, headers=_BROWSER_HEADERS)
                if r.status_code == 200:
                    print(f"[INFO] curl_cffi ({profile}) got 200 for {url}")
                    return r
                print(f"[WARN] curl_cffi ({profile}) got {r.status_code}")
            except Exception as e:
                print(f"[WARN] curl_cffi ({profile}) failed: {e}")
                break  # ImportError → no point retrying
        return None

    # --- source 1: RSS feed ---
    articles_raw: list[dict] = []
    resp = _get(RSS_FEED)
    if resp and len(resp.text) > 200:
        print(f"[INFO] RSS fetched ({len(resp.text)} bytes)")
        ns = {"content": "http://purl.org/rss/1.0/modules/content/"}
        try:
            root = ET.fromstring(resp.text)
            for item in root.findall(".//item"):
                link = (item.findtext("link") or "").strip()
                if not link:
                    continue
                title = (item.findtext("title") or "").strip()
                pub_date = (item.findtext("pubDate") or "").strip()
                description_html = (item.findtext("description") or "").strip()
                content_html = (item.findtext("content:encoded", namespaces=ns) or "").strip()
                date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if pub_date:
                    try:
                        from email.utils import parsedate_to_datetime
                        date_str = parsedate_to_datetime(pub_date).strftime("%Y-%m-%d %H:%M:%S")
                    except Exception:
                        pass
                desc_text = ""
                if description_html:
                    try:
                        desc_text = BS(description_html, "html.parser").get_text(separator="\n", strip=True)
                    except Exception:
                        desc_text = description_html
                image_url = ""
                if content_html:
                    m = _re.search(r'<img[^>]+src=["\']([^"\']+)["\']', content_html)
                    if m:
                        image_url = m.group(1)
                articles_raw.append({"title": title, "link": link, "summary": desc_text,
                                     "description": desc_text, "date": date_str,
                                     "image_url": image_url, "date_source": f"RSS: {date_str}"})
            print(f"[INFO] RSS parsed {len(articles_raw)} items")
        except ET.ParseError as e:
            print(f"[WARN] RSS parse error: {e}")

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

    # --- source 3: Google News RSS (served from Google CDN, never CF-blocked) ---
    if not articles_raw:
        import base64 as _b64
        print("[INFO] WP-API unavailable — trying Google News RSS...")

        def _decode_gnews_url(gnews_url: str) -> str:
            """Extract original article URL from a Google News redirect link."""
            m = _re.search(r"/articles/([^?&#]+)", gnews_url)
            if not m:
                return gnews_url
            try:
                padded = m.group(1) + "=" * (-len(m.group(1)) % 4)
                raw = _b64.urlsafe_b64decode(padded)
                urls = _re.findall(rb"https?://[^\x00-\x1f\x7f-\xff ]+", raw)
                if urls:
                    return urls[0].decode("utf-8").rstrip(".")
            except Exception:
                pass
            return gnews_url

        resp = _get(GNEWS_RSS)
        if resp and len(resp.text) > 200:
            try:
                root = ET.fromstring(resp.text)
                for item in root.findall(".//item"):
                    gnews_link = (item.findtext("link") or "").strip()
                    if not gnews_link:
                        continue
                    link = _decode_gnews_url(gnews_link)
                    # keep only economynext.com links
                    if "economynext.com" not in link:
                        link = gnews_link
                    title = (item.findtext("title") or "").strip()
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

    if not articles_raw:
        print("[ERROR] All sources failed — saving empty list")
        save_replace_only(json_path, [])
        return 0

    # --- apply checkpoint / dedup / cap ---
    new_articles: list[dict] = []
    seen_this_run: set[str] = set()

    for art in articles_raw:
        link = art["link"]
        norm = normalize_link(link)

        if is_last_scraped_article(link, checkpoint_link):
            print(f"[INCREMENTAL] Reached last scraped article — stopping.\n             {link}")
            break

        if norm in known_previous:
            print(f"[SKIP] Already in previous run: {link[:80]}")
            continue

        if norm in seen_this_run:
            continue

        if not art.get("title") and not art.get("summary"):
            print(f"[SKIP] Empty row: {link[:80]}")
            continue

        new_articles.append(art)
        seen_this_run.add(norm)
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
        INCREMENTAL_BOOTSTRAP_LIMIT,
        INCREMENTAL_RUN_LIMIT,
        get_section_checkpoint,
        load_known_links,
        normalize_link,
        save_replace_only,
        update_section_checkpoints,
    )
    from incremental_runner import create_standard_driver

    mod = _import_scraper("themorning_selenium_json")
    cats = ["news", "opinion", "business", "features", "sports", "world"]
    pages = [(c, f"https://www.themorning.lk/categories/{c}") for c in cats]
    json_path = data_json_path("themorning_latest_news.json")

    # bootstrap = no section has a checkpoint yet
    bootstrap = not any(get_section_checkpoint(json_path, c)[0] for c in cats)
    max_articles = INCREMENTAL_BOOTSTRAP_LIMIT if bootstrap else INCREMENTAL_RUN_LIMIT
    known_previous = load_known_links(json_path)
    if known_previous:
        print(f"[INCREMENTAL] Skipping {len(known_previous)} URL(s) from previous file")

    print("[INCREMENTAL] The Morning — per-section checkpoints")
    if bootstrap:
        print(f"[INCREMENTAL] No checkpoint; bootstrap max {max_articles} articles")
    else:
        print(f"[INCREMENTAL] Run safety cap: {max_articles} new articles")

    driver = create_standard_driver(use_undetected=False)

    # Phase 1: collect links for every section; seed any missing checkpoints
    section_links: dict[str, list[str]] = {}
    for cat, url in pages:
        print(f"\n[PHASE 1] {cat}: {url}")
        try:
            driver.get(url)
            time.sleep(3)
            links = mod.get_main_article_links(driver)
            section_links[cat] = links
            print(f"  {len(links)} links")
            existing_ckpt, _ = get_section_checkpoint(json_path, cat)
            if not existing_ckpt and links:
                update_section_checkpoints(json_path, {cat: (links[0], "")})
                print(f"  [SEED] {links[0][:80]}")
        except Exception as e:
            print(f"  [ERROR] {e}")
            section_links[cat] = []

    # Phase 2: fetch new articles per section up to cap
    new_articles: list[dict] = []
    seen_this_run: set[str] = set()
    cap_hit = False

    for cat, _url in pages:
        if cap_hit:
            break
        links = section_links.get(cat, [])
        sec_ckpt, _ = get_section_checkpoint(json_path, cat)
        print(f"\n[PHASE 2] {cat} — checkpoint: {(sec_ckpt or 'None')[:70]}")

        for link in links:
            norm = normalize_link(link)
            if sec_ckpt and normalize_link(sec_ckpt) == norm:
                print(f"  [STOP] Reached section checkpoint")
                break
            if norm in known_previous or norm in seen_this_run:
                continue
            try:
                meta = _fetch_metadata(driver, link, mod)
                if meta and (meta.get("title") or meta.get("summary")):
                    new_articles.append(meta)
                    seen_this_run.add(norm)
                    update_section_checkpoints(json_path, {cat: (link, meta.get("title", ""))})
                    print(f"  [+] {meta.get('title', '')[:70]}")
            except Exception as e:
                print(f"  [ERROR] {e}")

            if len(new_articles) >= max_articles:
                label = "Bootstrap" if bootstrap else "Run safety"
                print(f"[INCREMENTAL] {label} limit ({max_articles}) reached.")
                cap_hit = True
                break

            time.sleep(0.5)

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
    mod = _import_scraper("dinamina_selenium_json")
    cats = ["local", "politics", "editorial", "sports", "features", "business"]
    pages = [(c, f"https://www.dinamina.lk/category/{c}/") for c in cats]
    collect = lambda d, u: collect_wp_category_links(d, u, "dinamina")

    return run_incremental_scraper(
        outlet_name="Dinamina",
        data_filename="dinamina_latest_news.json",
        pages=pages,
        collect_links=collect,
        fetch_article=lambda d, l: _fetch_content(d, l, mod),
        use_undetected=True,
    )


def run_divaina_incremental() -> int:
    mod = _import_scraper("divaina_selenium_json")
    pages = [
        ("breaking", mod.BASE_URL),
        ("main", mod.MAIN_NEWS_URL),
        ("provincial", mod.PROVINCIAL_NEWS_URL),
    ]

    def collect(d, url):
        if "main-news" in url or "provincial" in url:
            return collect_divaina_main_links(d, url)
        return collect_divaina_breaking_links(d, url)

    return run_incremental_scraper(
        outlet_name="Divaina",
        data_filename="divaina_latest_news.json",
        pages=pages,
        collect_links=collect,
        fetch_article=lambda d, l: _fetch_content(d, l, mod),
        use_undetected=True,
    )


def run_lankadeepa_incremental() -> int:
    mod = _import_scraper("lankadeepa_selenium_json")
    pages = [
        ("latest", "https://www.lankadeepa.lk/latest-news/1"),
        ("features", "https://www.lankadeepa.lk/features/2"),
        ("politics", "https://www.lankadeepa.lk/politics/13"),
        ("sports", "https://www.lankadeepa.lk/sports/7"),
        ("world", "https://www.lankadeepa.lk/world/8"),
    ]

    return run_incremental_scraper(
        outlet_name="Lankadeepa",
        data_filename="lankadeepa_latest_news.json",
        pages=pages,
        collect_links=collect_lankadeepa_links,
        fetch_article=lambda d, l: _fetch_content(d, l, mod),
        use_undetected=True,
    )


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
