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
    collect_economynext_homepage_links,
    collect_economynext_list_links,
    collect_lankadeepa_links,
    collect_thamilan_links,
    collect_virakesari_links,
    collect_wp_category_links,
)
from incremental_runner import (
    article_from_content,
    article_from_metadata,
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


def _economynext_extract_from_soup(soup, link: str) -> dict | None:
    """Extract EconomyNext article fields from a BeautifulSoup object (static HTML)."""
    from datetime import datetime
    import json as _json

    # Title
    title = ""
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(strip=True)
    if not title:
        og = soup.find("meta", attrs={"property": "og:title"})
        if og:
            title = og.get("content", "").strip()

    # Date
    date_published = None
    date_meta = soup.find("meta", attrs={"property": "article:published_time"})
    if date_meta and date_meta.get("content"):
        date_str = date_meta["content"]
        if "," in date_str:
            date_part = date_str.split(",", 1)[1].strip()
            for fmt in ["%A %B %d, %Y", "%A %b %d, %Y"]:
                try:
                    date_published = datetime.strptime(date_part, fmt)
                    break
                except ValueError:
                    continue
    if not date_published:
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = _json.loads(script.string or "")
                if isinstance(data, dict) and "datePublished" in data:
                    date_published = datetime.fromisoformat(
                        data["datePublished"].replace("Z", "").split("+")[0]
                    )
                    break
            except Exception:
                continue

    # Image
    image_url = ""
    og_img = soup.find("meta", attrs={"property": "og:image"})
    if og_img:
        image_url = og_img.get("content", "")

    # Description
    description = ""
    main_divs = [
        d for d in soup.find_all("div", class_="story-page-text-content")
        if "most-recent-article-text" not in " ".join(d.get("class", []))
    ] or soup.find_all("div", class_="story-page-text-content")
    if main_divs:
        paras = [p.get_text(strip=True) for p in main_divs[0].find_all("p") if p.get_text(strip=True)]
        description = "\n\n".join(paras) if paras else main_divs[0].get_text(strip=True)

    if not title and not description:
        return None

    standardized_date = (
        date_published.strftime("%Y-%m-%d %H:%M:%S")
        if date_published
        else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    return {
        "title": title,
        "link": link,
        "summary": description,
        "description": description,
        "date": standardized_date,
        "image_url": image_url,
        "date_source": f"Article page: {standardized_date}" if date_published else "Incremental scrape",
    }


def _economynext_fetch_article(driver, link: str, mod) -> dict | None:
    """Fetch EconomyNext article: try curl_cffi first (bypasses Cloudflare), fall back to Selenium."""
    # Try curl_cffi first — works from GHA datacenter IPs
    try:
        from curl_cffi import requests as cf_requests  # type: ignore
        from bs4 import BeautifulSoup as _BS

        resp = cf_requests.get(link, impersonate="chrome", timeout=30)
        if resp.status_code in (200, 202, 301, 302):
            soup = _BS(resp.text, "html.parser")
            title_tag = soup.find("title") or soup.find("h1")
            title_text = (title_tag.get_text(strip=True) if title_tag else "").lower()
            if "just a moment" not in title_text:
                result = _economynext_extract_from_soup(soup, link)
                if result and (result.get("title") or result.get("summary")):
                    print(f"     [curl_cffi] Extracted: {result.get('title', '')[:60]}")
                    return result
    except ImportError:
        pass
    except Exception as e:
        print(f"     [curl_cffi] Failed: {e}")

    # Fall back to Selenium
    return _fetch_metadata(driver, link, mod)


def run_economynext_incremental() -> int:
    mod = _import_scraper("economynext_selenium_json")
    pages = [
        ("homepage", "https://economynext.com/"),
        ("more-news", "https://economynext.com/more-news/"),
    ]

    def collect(d, url):
        if "more-news" in url:
            return collect_economynext_list_links(d, url)
        return collect_economynext_homepage_links(d, url)

    def fetch(d, link):
        return _economynext_fetch_article(d, link, mod)

    return run_incremental_scraper(
        outlet_name="EconomyNext",
        data_filename="economynext_latest_news.json",
        pages=pages,
        collect_links=collect,
        fetch_article=fetch,
        use_undetected=True,
    )


def run_themorning_incremental() -> int:
    mod = _import_scraper("themorning_selenium_json")
    cats = ["news", "opinion", "business", "features", "sports", "world"]
    pages = [(c, f"https://www.themorning.lk/categories/{c}") for c in cats]

    def collect(d, url):
        d.get(url)
        time.sleep(3)
        return mod.get_main_article_links(d)

    def fetch(d, link):
        return _fetch_metadata(d, link, mod)

    return run_incremental_scraper(
        outlet_name="The Morning",
        data_filename="themorning_latest_news.json",
        pages=pages,
        collect_links=collect,
        fetch_article=fetch,
        use_undetected=False,
    )


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
