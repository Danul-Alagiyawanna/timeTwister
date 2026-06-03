from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import time
import json
import os
import sys
from datetime import datetime, timedelta
import re

from incremental import (
    is_incremental_mode,
    get_last_scraped_checkpoint,
    is_last_scraped_article,
    save_replace_only,
    normalize_link,
)


def _data_json_path():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    return os.path.join(project_root, "data", "sundaytimes_latest_news.json")


def _create_driver():
    chrome_options = Options()
    chrome_options.page_load_strategy = 'eager'
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--disable-blink-features=AutomationControlled')
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    chrome_options.add_argument(
        '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    )
    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()), options=chrome_options,
    )
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return driver


def get_list_entries_from_page(driver, url):
    """Return article links from a category list page in DOM order (no article fetch)."""
    print(f"\n[INFO] Listing articles from: {url}")
    driver.get(url)
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CLASS_NAME, "entry_title"))
        )
    except Exception as e:
        print(f"[WARNING] List page not available: {e}")
        return []

    soup = BeautifulSoup(driver.page_source, 'html.parser')
    post_divs = soup.select('div.entry.loop-default.border_bottom_30')
    print(f"[INFO] Found {len(post_divs)} entries on list page")

    entries = []
    for post_div in post_divs:
        title_tag = post_div.find('h2', class_='entry_title')
        if not title_tag or not title_tag.find('a'):
            continue
        link = title_tag.find('a').get('href', '')
        if not link:
            continue
        summary_tag = post_div.find('p')
        img_tag = post_div.find('img')
        entries.append({
            'link': link,
            'list_title': title_tag.get_text(strip=True),
            'list_summary': summary_tag.get_text(strip=True) if summary_tag else '',
            'list_image_url': img_tag.get('src', '') if img_tag else '',
        })
    return entries


def extract_article_metadata(driver, url):
    """Extract detailed metadata from Sunday Times article page including exact publication date."""
    try:
        print(f"[INFO] Fetching metadata from: {url}")
        driver.get(url)
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        # Extract publication date using multiple strategies
        date_published = None
        
        # Strategy 1: Extract date from URL pattern (most reliable for Sunday Times)
        # URL format: https://www.sundaytimes.lk/250713/news/article-name.html
        url_date_match = re.search(r'/(\d{6})/', url)
        if url_date_match:
            try:
                date_str = url_date_match.group(1)  # e.g., "250713"
                # Convert YYMMDD to proper date (assuming 20XX for years)
                year = int("20" + date_str[:2])
                month = int(date_str[2:4])
                day = int(date_str[4:6])
                date_published = datetime(year, month, day)
                print(f"[INFO] Found date from URL: {date_str} -> {date_published.strftime('%Y-%m-%d')}")
            except ValueError as e:
                print(f"[WARNING] Error parsing URL date '{date_str}': {e}")
        
        # Strategy 2: Look for date in page submenu (li.date.right)
        if not date_published:
            date_element = soup.find('li', class_='date right')
            if date_element:
                date_text = date_element.get_text(strip=True)
                # Parse "Sunday, July 13, 2025"
                try:
                    # Remove day of week and parse the date
                    date_parts = date_text.split(', ', 1)
                    if len(date_parts) > 1:
                        date_str = date_parts[1]  # "July 13, 2025"
                        date_published = datetime.strptime(date_str, "%B %d, %Y")
                        print(f"[INFO] Found date from page content: {date_text}")
                except ValueError as e:
                    print(f"[WARNING] Error parsing page date '{date_text}': {e}")
        
        # Strategy 3: Try datePublished meta tag (fallback)
        if not date_published:
            date_meta = soup.find('meta', attrs={'itemprop': 'datePublished'})
            if date_meta and date_meta.has_attr('content'):
                try:
                    date_str = date_meta['content']
                    for date_format in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"]:
                        try:
                            date_published = datetime.strptime(date_str, date_format)
                            break
                        except ValueError:
                            continue
                    if date_published:
                        print(f"[INFO] Found date from meta tag: {date_meta['content']}")
                except ValueError as e:
                    print(f"[WARNING] Error parsing meta date '{date_meta['content']}': {e}")
        
        # Strategy 4: Try article:published_time meta tag
        if not date_published:
            date_meta = soup.find('meta', attrs={'property': 'article:published_time'})
            if date_meta and date_meta.has_attr('content'):
                try:
                    date_str = date_meta['content']
                    if 'T' in date_str:
                        date_str = date_str.split('T')[0] + ' ' + date_str.split('T')[1].split('+')[0].split('Z')[0]
                    date_published = datetime.fromisoformat(date_str.replace('Z', ''))
                    print(f"[INFO] Found date from article meta: {date_meta['content']}")
                except (ValueError, TypeError) as e:
                    print(f"[WARNING] Error parsing article date '{date_meta['content']}': {e}")
        
        if not date_published:
            print(f"[WARNING] No publication date found")
        
        # Extract image URL from OpenGraph meta tag
        image_url = ""
        og_image = soup.find('meta', attrs={'property': 'og:image'})
        if og_image and og_image.has_attr('content'):
            image_url = og_image['content']
            print(f"[INFO] Found image: {image_url[:60]}...")
        
        # Extract description from OpenGraph meta tag or content
        description = ""
        og_description = soup.find('meta', attrs={'property': 'og:description'})
        if og_description and og_description.has_attr('content'):
            description = og_description['content']
            print(f"[INFO] Found description: {description[:60]}...")
        else:
            # Fallback: try to find article content
            content_div = soup.find('div', class_=['entry', 'post'])
            if content_div:
                paragraphs = content_div.find_all('p')
                if paragraphs:
                    # Get first substantial paragraph
                    for p in paragraphs:
                        text = p.get_text(strip=True)
                        if len(text) > 50:  # Only take substantial content
                            description = text
                            print(f"[INFO] Found description (fallback): {description[:60]}...")
                            break
        
        # Extract title from OpenGraph meta tag
        title = ""
        og_title = soup.find('meta', attrs={'property': 'og:title'})
        if og_title and og_title.has_attr('content'):
            title = og_title['content']
        
        return {
            'date_published': date_published,
            'description': description,
            'image_url': image_url,
            'title': title
        }
        
    except Exception as e:
        print(f"[ERROR] Error extracting metadata from {url}: {e}")
        return {
            'date_published': None,
            'description': "",
            'image_url': "",
            'title': ""
        }

def is_article_in_date_range(article_date, start_date, end_date):
    """Check if article date falls within the specified range."""
    if not article_date:
        return False  # Exclude articles without parseable dates for accuracy
    
    # Convert to date only for comparison
    article_date = article_date.date()
    return start_date <= article_date <= end_date

def get_articles_from_page(driver, url, start_date, end_date):
    """Enhanced article extraction with date filtering."""
    print(f"\n[INFO] Processing articles from: {url}")
    driver.get(url)
    
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CLASS_NAME, "entry_title"))
        )
        print("[INFO] List page loaded successfully")
    except Exception as e:
        print(f"[WARNING] Timeout waiting for articles: {e}")
        return [], 0, 0
    
    soup = BeautifulSoup(driver.page_source, 'html.parser')
    post_divs = soup.select('div.entry.loop-default.border_bottom_30')
    print(f"[INFO] Found {len(post_divs)} articles on this page")
    
    articles_found = []
    articles_in_range = 0
    articles_outside_range = 0
    consecutive_outside_range = 0
    max_consecutive_outside = 2  # Stop if 2 consecutive articles are outside range
    
    for i, post_div in enumerate(post_divs, 1):
        try:
            # Title and link from list page
            title_tag = post_div.find('h2', class_='entry_title')
            if not title_tag:
                continue
                
            list_title = title_tag.get_text(strip=True)
            link = ''
            if title_tag and title_tag.find('a'):
                link = title_tag.find('a').get('href', '')
            
            if not link:
                continue
            
            print(f"\n[INFO] Article {i}/{len(post_divs)}: {list_title[:60]}...")
            
            # Extract metadata from individual article page
            metadata = extract_article_metadata(driver, link)
            
            # Check if article is in date range
            article_date = metadata['date_published']
            
            if article_date is None:
                print(f"[WARNING] No date found, skipping article")
                consecutive_outside_range += 1
            elif is_article_in_date_range(article_date, start_date, end_date):
                print(f"[INFO] Article is in date range!")
                
                # Get basic info from list page as fallback
                summary_tag = post_div.find('p')
                list_summary = summary_tag.get_text(strip=True) if summary_tag else ''
                
                img_tag = post_div.find('img')
                list_image_url = img_tag.get('src', '') if img_tag else ''
                
                # Use metadata if available, otherwise use list page data
                final_title = metadata['title'] if metadata['title'] else list_title
                final_description = metadata['description'] if metadata['description'] else list_summary
                final_image_url = metadata['image_url'] if metadata['image_url'] else list_image_url
                
                # Standardized date format
                standardized_date = article_date.strftime("%Y-%m-%d %H:%M:%S")
                
                articles_found.append({
                    'title': final_title,
                    'link': link,
                    'summary': final_description,
                    'image_url': final_image_url,
                    'date': standardized_date,
                    'date_source': f"Meta tag: {standardized_date}"
                })
                articles_in_range += 1
                consecutive_outside_range = 0  # Reset counter
            else:
                article_date_str = article_date.strftime('%Y-%m-%d')
                print(f"[INFO] Article outside date range: {article_date_str}")
                articles_outside_range += 1
                consecutive_outside_range += 1
                
                # Check if article is significantly older than our target range
                days_before_range = (start_date - article_date.date()).days
                if days_before_range > 2:
                    print(f"[INFO] Article is {days_before_range} days before target range - stopping immediately")
                    break
            
            # Check if we should stop (too many consecutive articles outside range)
            if consecutive_outside_range >= max_consecutive_outside:
                print(f"\n[INFO] Stopping: {max_consecutive_outside} consecutive articles outside date range")
                break
            
            time.sleep(0.5)  # Be polite between requests
            
        except Exception as e:
            print(f"[ERROR] Error processing article {i}: {e}")
            continue
    
    print(f"\n[INFO] Page summary: {articles_in_range} in range, {articles_outside_range} outside range")
    return articles_found, articles_in_range, articles_outside_range

def main_incremental():
    """Scrape until the last-scraped article URL is detected, then stop."""
    json_filename = _data_json_path()
    checkpoint_link, checkpoint_title = get_last_scraped_checkpoint(json_filename)
    bootstrap = not checkpoint_link
    bootstrap_limit = 40

    print("[INCREMENTAL] Sunday Times — stop when last scraped article is detected")
    if bootstrap:
        print(f"[INCREMENTAL] No prior data; bootstrap (max {bootstrap_limit} articles)")

    driver = _create_driver()
    base_url = "https://www.sundaytimes.lk"
    today = datetime.now().date()
    scrape_dates = [today, today - timedelta(days=1)]
    categories = [
        "news", "business-times", "sports", "columns",
        "plus", "sunday-times-2", "international",
    ]

    new_articles = []
    seen_this_run = set()
    stop_all = False

    try:
        for current_date in scrape_dates:
            if stop_all:
                break
            date_str = current_date.strftime("%y%m%d")
            print(f"\n[INCREMENTAL] Date section: {current_date} ({date_str})")

            for cat in categories:
                if stop_all:
                    break
                page_url = f"{base_url}/{date_str}/{cat}/"
                entries = get_list_entries_from_page(driver, page_url)
                if not entries:
                    continue

                for i, entry in enumerate(entries, 1):
                    link = entry['link']
                    norm = normalize_link(link)

                    if is_last_scraped_article(link, checkpoint_link):
                        print(f"\n[INCREMENTAL] Reached last scraped article — stopping.")
                        print(f"             {link}")
                        stop_all = True
                        break

                    if norm in seen_this_run:
                        continue

                    print(f"\n[INFO] New article {i}: {entry['list_title'][:60]}...")
                    metadata = extract_article_metadata(driver, link)
                    article_date = metadata['date_published']
                    standardized_date = (
                        article_date.strftime("%Y-%m-%d %H:%M:%S")
                        if article_date
                        else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    )

                    new_articles.append({
                        'title': metadata['title'] or entry['list_title'],
                        'link': link,
                        'summary': metadata['description'] or entry['list_summary'],
                        'image_url': metadata['image_url'] or entry['list_image_url'],
                        'date': standardized_date,
                        'date_source': f"Meta tag: {standardized_date}" if article_date else "Incremental scrape",
                    })
                    seen_this_run.add(norm)

                    if bootstrap and len(new_articles) >= bootstrap_limit:
                        print(f"\n[INCREMENTAL] Bootstrap limit ({bootstrap_limit}) reached.")
                        stop_all = True
                        break

                    time.sleep(0.5)
    finally:
        driver.quit()

    print(f"\n[INCREMENTAL] New articles this run: {len(new_articles)}")
    save_replace_only(json_filename, new_articles)
    print("\n[INFO] Sunday Times incremental scraper finished.")


def main(start_date=None, end_date=None):
    """Enhanced main function with date range filtering."""
    
    # Default to yesterday and today if no dates provided
    if not start_date or not end_date:
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=1)
        print(f"[DATE] No date range provided, using default: {start_date} to {end_date}")
    else:
        print(f"[DATE] Scraping articles from {start_date} to {end_date}")
    
    print("[INFO] Starting Enhanced Sunday Times scraper with date filtering...")
    print("=" * 50)
    
    chrome_options = Options()
    chrome_options.page_load_strategy = 'eager'
    chrome_options.add_argument('--headless')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--disable-blink-features=AutomationControlled')
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
    
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
    
    # Add stealth settings
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    
    # Sunday Times uses date-based URLs - we'll need to construct URLs for different dates
    base_url = "https://www.sundaytimes.lk"
    all_articles = []
    total_articles_in_range = 0
    total_articles_outside_range = 0
    
    print(f"\n[INFO] Processing Sunday Times articles with date filtering...")
    
    try:
        # Generate URLs for different dates and categories in the range
        current_date = start_date
        categories = ["news", "business-times", "sports", "columns", "plus", "sunday-times-2", "international"]
        scraped_urls = set()
        
        while current_date <= end_date:
            date_str = current_date.strftime("%y%m%d")
            
            for cat in categories:
                # Sunday Times URL format: https://www.sundaytimes.lk/YYMMDD/category/
                url = f"{base_url}/{date_str}/{cat}/"
                print(f"\n[INFO] Checking: {current_date} / {cat} ({url})")
                
                try:
                    articles, page_in_range, page_outside_range = get_articles_from_page(
                        driver, url, start_date, end_date
                    )
                    
                    # Filter out duplicates
                    unique_articles = []
                    for article in articles:
                        if article['link'] not in scraped_urls:
                            unique_articles.append(article)
                            scraped_urls.add(article['link'])
                    
                    all_articles.extend(unique_articles)
                    total_articles_in_range += len(unique_articles)
                    total_articles_outside_range += page_outside_range
                    
                    print(f"[INFO] Found {len(unique_articles)} unique articles in range for {current_date} / {cat}")
                    
                    # Brief delay between categories
                    time.sleep(1)
                    
                except Exception as e:
                    print(f"[WARNING] No content or error for {current_date} / {cat}: {e}")
                
            current_date += timedelta(days=1)
        
    finally:
        driver.quit()

    # Enhanced results summary
    print("\n" + "=" * 50)
    print("[INFO] ENHANCED SCRAPING SUMMARY")
    print("=" * 50)
    print(f"[INFO] Articles in date range: {total_articles_in_range}")
    print(f"[INFO] Articles outside range: {total_articles_outside_range}")
    print(f"[INFO] Total articles to save: {len(all_articles)}")

    if all_articles:
        # Count articles with images
        articles_with_images = sum(1 for article in all_articles if article.get('image_url'))
        print(f"[INFO] Articles with images: {articles_with_images}/{len(all_articles)} ({articles_with_images/len(all_articles)*100:.1f}%)")

        # Show sample titles
        print(f"\n[INFO] Sample articles:")
        for i, article in enumerate(all_articles[:3], 1):
            title = article.get('title', 'Unknown')[:80]
            print(f"   {i}. {title}...")

    # Save to JSON file in the data directory (even if empty)
    data_dir = "data"
    os.makedirs(data_dir, exist_ok=True)
    json_filename = os.path.join(data_dir, "sundaytimes_latest_news.json")

    try:
        if not all_articles:
            print(f" [INFO] 0 articles scraped. Preserving existing data in {json_filename} intact.")
        else:
            with open(json_filename, 'w', encoding='utf-8') as jsonfile:
                json.dump(all_articles, jsonfile, ensure_ascii=False, indent=2)

        if not all_articles:
            print(f"[WARNING] No articles found in the specified date range ({start_date} to {end_date})")
            print(f"[INFO] Saved empty JSON array to {json_filename}")
            print("[INFO] Try expanding your date range or check if Sunday Times has articles for those dates")
        else:
            print(f"[INFO] Successfully saved {len(all_articles)} articles to {json_filename}")
            print(f"[INFO] File location: {os.path.abspath(json_filename)}")
            print(f"[INFO] Date range: {start_date} to {end_date}")

    except Exception as e:
        print(f"[ERROR] Error saving to file: {e}")

    print("\n[INFO] Sunday Times Enhanced Scraper completed successfully!")

if __name__ == "__main__":
    if is_incremental_mode():
        main_incremental()
        sys.exit(0)

    date_args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(date_args) >= 2:
        try:
            start_date = datetime.strptime(date_args[0], "%Y-%m-%d").date()
            end_date = datetime.strptime(date_args[1], "%Y-%m-%d").date()
            main(start_date, end_date)
        except ValueError as e:
            print(f"[ERROR] Invalid date format. Use YYYY-MM-DD. Error: {e}")
            print("[INFO] Example: python sundaytimes_selenium_json.py 2025-01-18 2025-01-19")
    else:
        main()