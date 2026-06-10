"""
EconomyNext scraper — uses Scrapling HTTP fetcher (no Selenium).
Incremental mode: RSS + WP API via incremental_outlets.run_economynext_incremental().
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta

from economynext_scrapling import (
    BASE_URL,
    fetch_article_metadata,
    fetch_text,
    is_cloudflare_block,
    parse_homepage_article_links,
    parse_list_page_cards,
)


def is_article_in_date_range(article_date, start_date, end_date):
    if not article_date:
        return False
    return start_date <= article_date.date() <= end_date


def process_homepage_articles(start_date, end_date, processed_urls):
    print(f"\n[INFO] Loading homepage: {BASE_URL}/")
    html = fetch_text(f"{BASE_URL}/", timeout=25)
    if not html:
        print("   Failed to load homepage.")
        return []

    filtered_links = parse_homepage_article_links(html)
    if not filtered_links:
        print("   No article links with IDs found on the homepage.")
        return []
    print(f"   Found {len(filtered_links)} articles on homepage (ID filter applied)")

    articles_found = []
    for idx, article_link in enumerate(filtered_links):
        normalized_url = article_link.rstrip("/")
        if normalized_url in processed_urls:
            continue
        processed_urls.add(normalized_url)
        processed_urls.add(normalized_url + "/")

        print(f"\n   Homepage Article {idx + 1}/{len(filtered_links)}: {article_link}")
        try:
            metadata = fetch_article_metadata(article_link)
            final_date = metadata["date_published"]
            if final_date is None:
                print("     [SKIP] No date found for article, skipping")
                continue
            if not is_article_in_date_range(final_date, start_date, end_date):
                print(f"     [SKIP] Date {final_date.strftime('%Y-%m-%d')} is outside range")
                continue
            standardized_date = final_date.strftime("%Y-%m-%d %H:%M:%S")
            articles_found.append(
                {
                    "title": metadata["title"],
                    "link": article_link,
                    "summary": metadata["description"],
                    "date": standardized_date,
                    "image_url": metadata["image_url"],
                    "date_source": "Article page (Homepage link)",
                }
            )
            print("     [ADDED] Article is in range and added successfully")
        except Exception as e:
            print(f"     Error processing homepage article: {e}")

    print(f"\n  Homepage scraping summary: Found {len(articles_found)} articles in range")
    return articles_found


def process_articles_from_list_page(list_url, start_date, end_date, processed_urls):
    print(f"\n[INFO] Processing articles from: {list_url}")
    html = fetch_text(list_url, timeout=25)
    if not html:
        print("   Failed to load list page.")
        return [], 0, 0
    if is_cloudflare_block(html):
        print("   [WARNING] Cloudflare block on list page — retrying...")
        time.sleep(5)
        html = fetch_text(list_url, timeout=30)
        if not html or is_cloudflare_block(html):
            return [], 0, 0

    prefetched_cards = parse_list_page_cards(html)
    print(f"   Found {len(prefetched_cards)} article cards on listing page")
    if not prefetched_cards:
        return [], 0, 0

    articles_found = []
    articles_in_range = 0
    articles_outside_range = 0
    consecutive_outside_range = 0
    max_consecutive_outside = 3

    for idx, prefetched in enumerate(prefetched_cards):
        try:
            title = prefetched["title"]
            article_link = prefetched["link"]
            date_text = prefetched["date_text"]
            image_url = prefetched["image_url"]

            if not article_link:
                continue

            normalized_url = article_link.rstrip("/")
            if normalized_url in processed_urls:
                print(f"\n   Article {idx + 1}: Skipping (already processed)")
                articles_in_range += 1
                continue

            print(f"\n   Article {idx + 1}: {title[:60]}...")
            print(f"     Link: {article_link}")
            print(f"     Date text: {date_text}")

            article_date = None
            if date_text:
                date_text_clean = date_text.strip()
                for fmt in (
                    "%B %d, %Y",
                    "%b %d, %Y",
                    "%Y-%m-%d",
                    "%d %B %Y",
                    "%d %b %Y",
                ):
                    try:
                        article_date = datetime.strptime(date_text_clean, fmt)
                        print(f"     Parsed date: {article_date.strftime('%Y-%m-%d')}")
                        break
                    except ValueError:
                        continue

            if article_date is not None:
                if not is_article_in_date_range(article_date, start_date, end_date):
                    article_date_str = article_date.strftime("%Y-%m-%d")
                    print(f"     [SKIP] Article outside date range: {article_date_str}")
                    articles_outside_range += 1
                    consecutive_outside_range += 1
                    days_before_range = (start_date - article_date.date()).days
                    if days_before_range > 2:
                        print(
                            f"     Article is {days_before_range} days before target range - stopping"
                        )
                        break
                    if consecutive_outside_range >= max_consecutive_outside:
                        print(
                            f"\n   Stopping: {max_consecutive_outside} consecutive articles outside range"
                        )
                        break
                    continue

            final_date = article_date
            final_title = title
            final_image = image_url
            final_description = ""

            try:
                print("     Fetching article page...")
                metadata = fetch_article_metadata(article_link)
                if metadata["date_published"]:
                    final_date = metadata["date_published"]
                if metadata["title"]:
                    final_title = metadata["title"]
                if metadata["image_url"]:
                    final_image = metadata["image_url"]
                if metadata["description"]:
                    final_description = metadata["description"]
            except Exception as e:
                print(f"     Error loading article page: {e}")

            if article_date is None:
                if final_date is None:
                    print("     [SKIP] Still no date found for article, skipping")
                    consecutive_outside_range += 1
                    continue
                if not is_article_in_date_range(final_date, start_date, end_date):
                    print(f"     [SKIP] Date {final_date.strftime('%Y-%m-%d')} is outside range")
                    articles_outside_range += 1
                    consecutive_outside_range += 1
                    continue

            standardized_date = final_date.strftime("%Y-%m-%d %H:%M:%S")
            articles_found.append(
                {
                    "title": final_title,
                    "link": article_link,
                    "summary": final_description,
                    "date": standardized_date,
                    "image_url": final_image,
                    "date_source": "Article page" if final_date != article_date else "List page",
                }
            )
            processed_urls.add(normalized_url)
            processed_urls.add(normalized_url + "/")
            articles_in_range += 1
            consecutive_outside_range = 0
        except Exception as e:
            print(f"     Error with article card: {e}")

    print(f"\n  Page summary: {articles_in_range} in range, {articles_outside_range} outside range")
    return articles_found, articles_in_range, articles_outside_range


def main(start_date=None, end_date=None):
    if not start_date or not end_date:
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=1)
        print(f"[DATE] No date range provided, using default: {start_date} to {end_date}")
    else:
        print(f"[DATE] Scraping articles from {start_date} to {end_date}")

    print("[INFO] Starting EconomyNext scraper (Scrapling HTTP)...")
    print("=" * 50)

    base_url = f"{BASE_URL}/more-news/"
    all_articles = []
    processed_urls: set[str] = set()
    total_articles_in_range = 0
    total_articles_outside_range = 0
    consecutive_empty_pages = 0
    max_consecutive_empty = 3
    max_pages = 8

    try:
        homepage_articles = process_homepage_articles(start_date, end_date, processed_urls)
        all_articles.extend(homepage_articles)
        total_articles_in_range += len(homepage_articles)
    except Exception as e:
        print(f"[ERROR] Error processing homepage articles: {e}")

    print("\n[INFO] Processing paginated articles page by page with date filtering...")
    for page_num in range(max_pages):
        list_url = base_url if page_num == 0 else f"{base_url}page/{page_num + 1}/"
        print(f"\n[INFO] Page {page_num + 1}:")
        try:
            articles, page_in_range, page_outside_range = process_articles_from_list_page(
                list_url, start_date, end_date, processed_urls
            )
            all_articles.extend(articles)
            total_articles_in_range += page_in_range
            total_articles_outside_range += page_outside_range
            if page_in_range == 0:
                consecutive_empty_pages += 1
                print(
                    f"  [WARNING] No articles in range ({consecutive_empty_pages}/{max_consecutive_empty})"
                )
                if consecutive_empty_pages >= max_consecutive_empty:
                    print(f"  [ERROR] Stopping after {max_consecutive_empty} empty pages")
                    break
            else:
                consecutive_empty_pages = 0
            time.sleep(1)
        except Exception as e:
            print(f"  [ERROR] Error processing page {page_num + 1}: {e}")

    print("\n" + "=" * 50)
    print("[INFO] SCRAPING SUMMARY")
    print("=" * 50)
    print(f"[INFO] Articles in date range: {total_articles_in_range}")
    print(f"[INFO] Articles outside range: {total_articles_outside_range}")
    print(f"[INFO] Total articles to save: {len(all_articles)}")

    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    data_dir = os.path.join(project_root, "data")
    os.makedirs(data_dir, exist_ok=True)
    json_filename = os.path.join(data_dir, "economynext_latest_news.json")

    try:
        if not all_articles:
            print(f" [INFO] 0 articles scraped. Preserving existing data in {json_filename}.")
        else:
            with open(json_filename, "w", encoding="utf-8") as jsonfile:
                json.dump(all_articles, jsonfile, ensure_ascii=False, indent=2)
            print(f"[INFO] Successfully saved {len(all_articles)} articles to {json_filename}")
    except Exception as e:
        print(f"[ERROR] Error saving to file: {e}")

    print("\n[SUCCESS] EconomyNext scraper completed.")


if __name__ == "__main__":
    _scraper_dir = os.path.dirname(os.path.abspath(__file__))
    if _scraper_dir not in sys.path:
        sys.path.insert(0, _scraper_dir)
    from incremental import is_incremental_mode

    if is_incremental_mode():
        from incremental_outlets import run_incremental_for_module

        run_incremental_for_module("economynext_selenium_json")
        sys.exit(0)

    if len(sys.argv) >= 3:
        try:
            start_date = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
            end_date = datetime.strptime(sys.argv[2], "%Y-%m-%d").date()
            main(start_date, end_date)
        except ValueError as e:
            print(f" Invalid date format. Use YYYY-MM-DD. Error: {e}")
            print(" Example: python economynext_selenium_json.py 2025-01-18 2025-01-19")
    else:
        main()
