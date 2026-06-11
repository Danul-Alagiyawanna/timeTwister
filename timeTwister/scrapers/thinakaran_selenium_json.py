"""
Thinakaran scraper — uses Scrapling HTTP fetcher (no Selenium on GHA).
Incremental mode: incremental_outlets.run_thinakaran_incremental().
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta

from scrapling_fetch import is_cloudflare_block
from scrapling_page import HtmlPage

from thinakaran_scrapling import (
    BASE_URL,
    THINAKARAN_SECTIONS,
    collect_category_links,
    extract_article_content_from_html,
    fetch_article_content,
    fetch_category_html,
    fetch_main_feed,
    fetch_section_feed,
    fetch_thinakaran_section_feed,
    parse_list_cards_from_html,
    parse_thinakaran_date,
)

if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


class TimeoutError(Exception):
    pass


def extract_article_content(driver, max_elapsed_time=30):
    url = getattr(driver, "current_url", "") or ""
    if isinstance(driver, HtmlPage):
        return extract_article_content_from_html(driver.page_source, url)

    if url.startswith("http"):
        content = fetch_article_content(url, timeout=max_elapsed_time)
        if content and (content.get("title") or content.get("description")):
            return content

    time.sleep(2)
    return extract_article_content_from_html(driver.page_source, url)


def extract_with_timeout(driver, timeout_seconds=30):
    start_time = time.time()
    try:
        result = extract_article_content(driver, max_elapsed_time=timeout_seconds)
        elapsed = time.time() - start_time
        if elapsed > timeout_seconds:
            print(f"     [TIMEOUT] Extraction took {elapsed:.1f}s")
            return None
        return result
    except TimeoutError:
        return None
    except Exception as e:
        if time.time() - start_time > timeout_seconds:
            print(f"     [TIMEOUT] Extraction failed: {e}")
            return None
        raise


def is_article_in_date_range(article_date, start_date, end_date):
    if not article_date:
        return False
    return start_date <= article_date.date() <= end_date


def process_articles_from_page(list_url, start_date, end_date, processed_urls=None):
    """Process one category page via Scrapling HTTP."""
    if processed_urls is None:
        processed_urls = set()

    print(f"\n[INFO] Processing articles from: {list_url}")
    html = fetch_category_html(list_url)
    if not html:
        print("   Failed to load list page.")
        return [], 0, 0
    if is_cloudflare_block(html):
        print("   [WARNING] Cloudflare block — retrying...")
        time.sleep(5)
        html = fetch_category_html(list_url, timeout=45)
        if not html or is_cloudflare_block(html):
            return [], 0, 0

    cards = parse_list_cards_from_html(html)
    print(f"   Found {len(cards)} articles in list container")
    if not cards:
        return [], 0, 0

    articles_found = []
    articles_in_range = 0
    articles_outside_range = 0
    consecutive_outside_range = 0
    max_consecutive_outside = 3

    for idx, card in enumerate(cards):
        article_link = card.get("link") or ""
        title = card.get("title") or ""
        article_date = card.get("date_published")
        date_text = card.get("date_text") or ""

        if not article_link or article_link in processed_urls:
            continue

        print(f"\n   Article {idx}: {title[:60]}...")
        print(f"     Link: {article_link}")
        print(f"     Date: {article_date.strftime('%Y-%m-%d') if article_date else date_text}")

        if article_date is None:
            print("     No date found, skipping article")
            consecutive_outside_range += 1
        elif not is_article_in_date_range(article_date, start_date, end_date):
            print(f"     [SKIP] Outside date range")
            articles_outside_range += 1
            consecutive_outside_range += 1
            days_before = (start_date - article_date.date()).days
            if days_before > 2:
                break
            if consecutive_outside_range >= max_consecutive_outside:
                break
            continue
        else:
            final_date = article_date
            final_title = title
            final_image = ""
            final_description = ""
            extraction_successful = False

            print("     Fetching article page...")
            article_content = fetch_article_content(article_link)
            if article_content:
                if article_content.get("date_published"):
                    final_date = article_content["date_published"]
                if article_content.get("title"):
                    final_title = article_content["title"]
                if article_content.get("image_url"):
                    final_image = article_content["image_url"]
                extracted = (article_content.get("description") or "").strip()
                if extracted:
                    final_description = extracted
                    extraction_successful = True

            standardized_date = final_date.strftime("%Y-%m-%d %H:%M:%S")
            articles_found.append(
                {
                    "title": final_title,
                    "link": article_link,
                    "summary": final_description,
                    "date": standardized_date,
                    "image_url": final_image,
                    "date_source": "Article page" if extraction_successful else f"List: {date_text}",
                }
            )
            processed_urls.add(article_link)
            articles_in_range += 1
            consecutive_outside_range = 0

    print(f"\n   Page summary: {articles_in_range} in range, {articles_outside_range} outside range")
    return articles_found, articles_in_range, articles_outside_range


def create_driver(headless=None):
    """Selenium fallback only when Scrapling fails."""
    try:
        import undetected_chromedriver as uc  # type: ignore

        use_undetected = True
    except Exception:
        use_undetected = False

    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager

    if headless is None:
        headless = os.getenv("CI", "").lower() in ("1", "true", "yes")

    prefs = {"profile.default_content_setting_values": {"popups": 1}}

    if use_undetected:
        def _uc_options():
            opts = uc.ChromeOptions()
            opts.page_load_strategy = "eager"
            if headless:
                opts.add_argument("--headless=new")
            opts.add_argument("--no-sandbox")
            opts.add_argument("--disable-dev-shm-usage")
            opts.add_experimental_option("prefs", prefs)
            return opts

        try:
            driver = uc.Chrome(options=_uc_options(), use_subprocess=True)
        except Exception as e:
            match = re.search(r"Current browser version is (\d+)", str(e)) or re.search(
                r"only supports Chrome version (\d+)", str(e)
            )
            if match:
                driver = uc.Chrome(
                    options=_uc_options(),
                    use_subprocess=True,
                    version_main=int(match.group(1)),
                )
            else:
                raise
    else:
        chrome_options = Options()
        chrome_options.page_load_strategy = "eager"
        if headless:
            chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_experimental_option("prefs", prefs)
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=chrome_options,
        )

    driver.set_page_load_timeout(90 if headless else 60)
    return driver


def main(start_date=None, end_date=None):
    if not start_date or not end_date:
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=1)
        print(f"[DATE] No date range provided, using default: {start_date} to {end_date}")
    else:
        print(f"[DATE] Scraping articles from {start_date} to {end_date}")

    print("[INFO] Starting Thinakaran scraper (Scrapling HTTP)...")

    categories = list(THINAKARAN_SECTIONS) + ["world"]
    scraped_urls: set[str] = set()
    all_articles: list[dict] = []
    total_in_range = 0

    for category in categories:
        cat_url = f"{BASE_URL}/category/{category}/"
        print(f"\n[INFO] === Category: {category} ({cat_url}) ===")
        try:
            articles, page_in_range, _ = process_articles_from_page(
                cat_url, start_date, end_date, scraped_urls
            )
            all_articles.extend(articles)
            total_in_range += page_in_range
            print(f"[INFO] Category '{category}': {page_in_range} in range")
        except Exception as e:
            print(f"  [ERROR] Category {category}: {e}")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(os.path.dirname(script_dir), "data")
    os.makedirs(data_dir, exist_ok=True)
    json_filename = os.path.join(data_dir, "thinakaran_latest_news.json")

    if all_articles:
        with open(json_filename, "w", encoding="utf-8") as f:
            json.dump(all_articles, f, ensure_ascii=False, indent=2)
        print(f"\n[INFO] Saved {len(all_articles)} articles to {json_filename}")
    else:
        print(f"\n[INFO] 0 articles — preserving existing {json_filename}")


if __name__ == "__main__":
    _scraper_dir = os.path.dirname(os.path.abspath(__file__))
    if _scraper_dir not in sys.path:
        sys.path.insert(0, _scraper_dir)
    from incremental import is_incremental_mode

    if is_incremental_mode():
        from incremental_outlets import run_incremental_for_module

        run_incremental_for_module("thinakaran_selenium_json")
        sys.exit(0)

    if len(sys.argv) >= 3:
        try:
            start_date = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
            end_date = datetime.strptime(sys.argv[2], "%Y-%m-%d").date()
            main(start_date, end_date)
        except ValueError as e:
            print(f"[ERROR] Invalid date format: {e}")
    else:
        main()
