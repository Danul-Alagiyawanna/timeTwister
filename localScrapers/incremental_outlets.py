"""
Incremental (real-time) scrapers for all localScrapers outlets.

Usage from any scraper:
  python thinakaran_selenium_json.py --incremental

Or run all:
  python run_scrapers.py --incremental
"""
from __future__ import annotations

import importlib
import os
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, Callable

_SCRAPERS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRAPERS_DIR not in sys.path:
    sys.path.insert(0, _SCRAPERS_DIR)

from incremental import (
    INCREMENTAL_BOOTSTRAP_LIMIT,
    INCREMENTAL_RUN_LIMIT,
    get_last_scraped_checkpoint,
    merge_and_save,
    normalize_link,
)
from incremental_links import (
    collect_ceylontoday_links,
    collect_dailynews_links,
    collect_dinamina_links,
    collect_module_list_links,
    collect_thinakaran_links,
)
from incremental_runner import (
    article_from_content,
    article_from_metadata,
    outlet_data_path,
    run_incremental_scraper,
)


def _import_scraper(module_name: str):
    return importlib.import_module(module_name)


def _fetch_metadata(driver, link: str, mod: Any, *, use_timeout: bool = False) -> dict | None:
    driver.get(link)
    time.sleep(2)
    if use_timeout:
        meta = mod.extract_with_timeout(driver)
        if meta:
            return article_from_content(meta, link)
        return None
    meta = mod.extract_article_metadata(driver)
    if not meta:
        return None
    return article_from_metadata(meta, link)


def run_dailynews_incremental() -> int:
    mod = _import_scraper("dailynews_selenium_json")
    cats = ["local", "politics", "business", "lawnorder", "world", "sports"]
    pages = [(c, f"https://dailynews.lk/category/{c}/") for c in cats]
    return run_incremental_scraper(
        outlet_name="Daily News",
        json_path=outlet_data_path("dailynews_latest_news.json"),
        pages=pages,
        collect_links=collect_dailynews_links,
        fetch_article=lambda d, l: _fetch_metadata(d, l, mod),
        use_undetected=True,
    )


def run_ceylontoday_incremental() -> int:
    from bs4 import BeautifulSoup

    mod = _import_scraper("ceylontoday_selenium_json")
    cats = ["news", "columns", "features", "sports", "world", "business"]
    pages = [
        (c, f"https://ceylontoday.lk/category/ceylon-today-daily/{c}/") for c in cats
    ]

    def fetch(d, link):
        d.get(link)
        time.sleep(2)
        soup = BeautifulSoup(d.page_source, "html.parser")
        title_el = soup.select_one("h1.entry-title") or soup.find("h1")
        title = title_el.get_text(strip=True) if title_el else ""
        date_tag = soup.find("time", class_="entry-date") or soup.find(
            "meta", property="article:published_time"
        )
        date_str = ""
        if date_tag:
            date_str = (
                date_tag.get("datetime")
                or date_tag.get("content")
                or date_tag.get_text(strip=True)
            )
        desc, image_url = mod.get_enhanced_article_description(d, link)
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

    return run_incremental_scraper(
        outlet_name="Ceylon Today",
        json_path=outlet_data_path("ceylontoday_finance.json"),
        pages=pages,
        collect_links=collect_ceylontoday_links,
        fetch_article=fetch,
        create_driver=mod.setup_driver,
        use_undetected=False,
    )


def _parse_economynext_rss(xml_text: str) -> list[dict[str, Any]]:
    from bs4 import BeautifulSoup as BS

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
        content_html = (item.findtext("content:encoded", namespaces=ns) or "").strip()
        date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if pub_date:
            try:
                date_str = parsedate_to_datetime(pub_date).strftime("%Y-%m-%d %H:%M:%S")
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
            import re

            m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', content_html)
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
                "date_source": f"RSS: {date_str}",
            }
        )
    return articles


def run_economynext_incremental() -> int:
    import requests

    from incremental import should_stop_at_feed_item, load_incremental_boundary_links

    json_path = outlet_data_path("economynext_latest_news.json")
    checkpoint_link, known_previous = load_incremental_boundary_links(json_path)
    bootstrap = not checkpoint_link
    max_articles = INCREMENTAL_BOOTSTRAP_LIMIT if bootstrap else INCREMENTAL_RUN_LIMIT

    print("[INCREMENTAL] EconomyNext — RSS feed (newest first)")
    try:
        resp = requests.get(
            "https://economynext.com/feed/",
            timeout=25,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        resp.raise_for_status()
        articles_raw = _parse_economynext_rss(resp.text)
    except Exception as e:
        print(f"[ERROR] RSS fetch failed: {e}")
        return 0

    if not articles_raw:
        print("[ERROR] No articles in RSS feed")
        return 0

    articles_raw.sort(key=lambda a: a.get("date", ""), reverse=True)
    new_articles: list[dict] = []
    seen: set[str] = set()

    for art in articles_raw:
        link = art.get("link", "")
        stop = should_stop_at_feed_item(
            link,
            checkpoint_link=checkpoint_link,
            known_previous=known_previous,
        )
        if stop:
            print(
                f"[INCREMENTAL] Reached checkpoint ({stop}) — stopping.\n"
                f"             {art.get('title', '')[:70]}"
            )
            break
        norm = normalize_link(link)
        if norm in seen:
            continue
        if not art.get("title") and not art.get("summary"):
            continue
        new_articles.append(art)
        seen.add(norm)
        print(f"[INFO] +Article: {art['title'][:70]}")
        if len(new_articles) >= max_articles:
            print(f"[INCREMENTAL] Safety limit ({max_articles}) reached.")
            break

    if new_articles:
        merge_and_save(json_path, new_articles)
    else:
        print("[INCREMENTAL] No new articles — archive unchanged")
    print(f"[INCREMENTAL] EconomyNext finished ({len(new_articles)} new).")
    return len(new_articles)


def run_ftlk_incremental() -> int:
    mod = _import_scraper("ftlk_selenium_json")
    return mod.main_incremental()


def run_island_incremental() -> int:
    mod = _import_scraper("island_selenium_json")
    pages = [
        ("news", "https://island.lk/category/news/"),
        ("business", "https://island.lk/category/business/"),
        ("sports", "https://island.lk/category/sports/"),
        ("politics", "https://island.lk/category/politics/"),
        ("features", "https://island.lk/category/features/"),
        ("opinion", "https://island.lk/category/opinion/"),
    ]
    return run_incremental_scraper(
        outlet_name="The Island",
        json_path=outlet_data_path("island_latest_news.json", use_parent=True),
        pages=pages,
        collect_links=lambda d, u: collect_module_list_links(d, u, mod),
        fetch_article=lambda d, l: _fetch_metadata(d, l, mod),
        use_undetected=True,
    )


def run_sundayobserver_incremental() -> int:
    mod = _import_scraper("sundayobserver_selenium_json")
    pages = [
        ("news", "https://www.sundayobserver.lk/category/news/"),
        ("business", "https://www.sundayobserver.lk/category/business/"),
        ("sports", "https://www.sundayobserver.lk/category/sports/"),
    ]
    return run_incremental_scraper(
        outlet_name="Sunday Observer",
        json_path=outlet_data_path("sundayobserver_latest_news.json", use_parent=True),
        pages=pages,
        collect_links=lambda d, u: collect_module_list_links(d, u, mod),
        fetch_article=lambda d, l: _fetch_metadata(d, l, mod),
        use_undetected=True,
    )


def run_thinakaran_incremental() -> int:
    mod = _import_scraper("thinakaran_selenium_json")
    categories = [
        "local", "politics", "features", "editorial", "sports", "business", "world"
    ]
    pages = [
        (c, f"https://www.thinakaran.lk/category/{c}/") for c in categories
    ]
    return run_incremental_scraper(
        outlet_name="Thinakaran",
        json_path=outlet_data_path("thinakaran_latest_news.json", use_parent=True),
        pages=pages,
        collect_links=collect_thinakaran_links,
        fetch_article=lambda d, l: _fetch_metadata(d, l, mod, use_timeout=True),
        use_undetected=True,
    )


def run_dinamina_incremental() -> int:
    mod = _import_scraper("dinamina_selenium_json")
    categories = [
        "local", "politics", "editorial", "sports", "features", "business", "world"
    ]
    pages = [(c, f"https://www.dinamina.lk/category/{c}/") for c in categories]
    return run_incremental_scraper(
        outlet_name="Dinamina",
        json_path=outlet_data_path("dinamina_latest_news.json", use_parent=True),
        pages=pages,
        collect_links=collect_dinamina_links,
        fetch_article=lambda d, l: _fetch_metadata(d, l, mod, use_timeout=True),
        use_undetected=True,
    )


INCREMENTAL_BY_MODULE: dict[str, Callable[[], int]] = {
    "dailynews_selenium_json": run_dailynews_incremental,
    "ceylontoday_selenium_json": run_ceylontoday_incremental,
    "economynext_selenium_json": run_economynext_incremental,
    "ftlk_selenium_json": run_ftlk_incremental,
    "island_selenium_json": run_island_incremental,
    "sundayobserver_selenium_json": run_sundayobserver_incremental,
    "thinakaran_selenium_json": run_thinakaran_incremental,
    "dinamina_selenium_json": run_dinamina_incremental,
}


def run_incremental_for_module(module_name: str) -> int:
    base = module_name.replace(".py", "").split(".")[-1]
    if base == "__main__":
        base = os.path.splitext(os.path.basename(sys.argv[0]))[0]
    fn = INCREMENTAL_BY_MODULE.get(base)
    if not fn:
        raise ValueError(f"No incremental runner for module: {module_name}")
    return fn()
