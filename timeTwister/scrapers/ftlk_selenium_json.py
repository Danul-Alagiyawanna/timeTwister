from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
import time
import re
import json
import os
import sys
from datetime import datetime, timedelta

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
    return os.path.join(project_root, "data", "ftlk_latest_news.json")


def _create_driver(headless=True):
    chrome_options = Options()
    chrome_options.page_load_strategy = 'eager'
    if headless:
        chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    chrome_options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=chrome_options,
    )

    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return driver


def get_list_entries_from_page(driver, url):
    """Collect article links from a list page in DOM order (no article fetch)."""
    print(f"\n[INFO] Listing articles from: {url}")
    driver.get(url)
    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        time.sleep(3)
    except Exception as e:
        print(f"[WARNING] List page not available: {e}")
        return []

    soup = BeautifulSoup(driver.page_source, 'html.parser')
    article_links = []

    article_divs = soup.find_all('div', class_='lineg')
    if article_divs:
        for div in article_divs:
            h3_tag = div.find('h3', class_='newsch')
            if h3_tag:
                link_tag = h3_tag.find_parent('a', href=True)
                if link_tag:
                    title = h3_tag.get_text(strip=True)
                    link = link_tag['href']
                    if link.startswith('/'):
                        link = 'https://www.ft.lk' + link
                    article_links.append({'title': title, 'link': link})

    if not article_links:
        all_links = soup.find_all('a', href=True)
        for link_tag in all_links:
            href = link_tag['href']
            if ('/news/' in href or '/front-page/' in href or '/business/' in href) and href.count('/') >= 3:
                title = link_tag.get_text(strip=True)
                if title and len(title) > 10:
                    if href.startswith('/'):
                        href = 'https://www.ft.lk' + href
                    if not any(a['link'] == href for a in article_links):
                        article_links.append({'title': title, 'link': href})

    print(f"[INFO] Found {len(article_links)} links on list page")
    return article_links


def extract_article_metadata(driver):
    """Extract detailed metadata from current FT.lk article page using XPath."""
    try:
        # Wait for page to load
        time.sleep(2)

        soup = BeautifulSoup(driver.page_source, 'html.parser')

        # Extract date using XPath
        date_published = None
        date_xpath = "/html/body/div[3]/div[3]/div[1]/div[3]/div/div[1]/div/p/span[1]"
        try:
            date_element = driver.find_element(By.XPATH, date_xpath)
            date_text = date_element.text.strip()
            print(f"     Found date text: {date_text}")

            # Try to parse the date
            date_formats = [
                "%A, %d %B %Y %H:%M",    # Monday, 24 November 2025 06:10
                "%A, %d %b %Y %H:%M",    # Monday, 24 Nov 2025 06:10
                "%B %d, %Y",              # January 18, 2025
                "%b %d, %Y",              # Jan 18, 2025
                "%Y-%m-%d",               # 2025-01-18
                "%d %B %Y",               # 18 January 2025
                "%d %b %Y",               # 18 Jan 2025
            ]

            for fmt in date_formats:
                try:
                    date_published = datetime.strptime(date_text, fmt)
                    print(f"     Parsed date: {date_published.strftime('%Y-%m-%d %H:%M:%S')}")
                    break
                except:
                    continue
        except Exception as e:
            print(f"     Could not extract date using XPath: {e}")

        # Extract image using XPath
        image_url = ""
        image_xpath = "/html/body/div[3]/div[3]/div[1]/header[2]/p[1]/img"
        try:
            image_element = driver.find_element(By.XPATH, image_xpath)
            image_url = image_element.get_attribute('src')
            if image_url:
                print(f"     Found image: {image_url[:60]}...")
        except:
            print(f"     No image found (this is normal for some articles)")

        # Extract full article content
        full_article_text = ""
        article_content_xpaths = [
            "/html/body/div[3]/div[3]/div[1]/div[3]/div",
            "/html/body/div[3]/div[3]/div[1]/div[3]",
            "/html/body/div[3]/div[3]/div[1]"
        ]
        
        for xpath in article_content_xpaths:
            try:
                content_element = driver.find_element(By.XPATH, xpath)
                # Try to get paragraphs within the content
                paragraphs = content_element.find_elements(By.TAG_NAME, 'p')
                if paragraphs:
                    full_article_text = '\n\n'.join([p.text.strip() for p in paragraphs if p.text.strip()])
                    if full_article_text and len(full_article_text) > 100:
                        print(f"     Found article content: {len(full_article_text)} characters")
                        break
            except:
                continue
        
        # If paragraph extraction didn't work, try getting all text
        if not full_article_text or len(full_article_text) < 100:
            try:
                content_element = driver.find_element(By.XPATH, "/html/body/div[3]/div[3]/div[1]/div[3]/div")
                full_article_text = content_element.text.strip()
                if full_article_text:
                    print(f"     Extracted full text from div: {len(full_article_text)} characters")
            except:
                pass

        # Extract description using XPath (try multiple patterns) - use as fallback if no full text
        description = ""
        if not full_article_text or len(full_article_text) < 100:
            description_xpaths = [
                "/html/body/div[3]/div[3]/div[1]/header[2]/p[3]",
                "/html/body/div[3]/div[3]/div[1]/header[2]/p",
                "/html/body/div[3]/div[3]/div[1]/header[2]/p[2]",
                "/html/body/div[3]/div[3]/div[1]/header[2]/p[1]"
            ]

            for xpath in description_xpaths:
                try:
                    desc_element = driver.find_element(By.XPATH, xpath)
                    description = desc_element.text.strip()
                    if description and len(description) > 30:  # Valid description found
                        print(f"     Found description from XPath: {len(description)} characters")
                        break
                except:
                    continue

            # Fallback: Try OpenGraph description if XPath didn't work
            if not description:
                og_description = soup.find('meta', attrs={'property': 'og:description'})
                if og_description and og_description.has_attr('content'):
                    description = og_description['content']
                    print(f"     Found description from meta tag: {description[:60]}...")
        
        # Use full article text if available, otherwise use description
        final_content = full_article_text if full_article_text and len(full_article_text) > 100 else description

        # Extract title from OpenGraph meta tag
        title = ""
        og_title = soup.find('meta', attrs={'property': 'og:title'})
        if og_title and og_title.has_attr('content'):
            title = og_title['content']

        # Get current URL
        current_url = driver.current_url

        return {
            'date_published': date_published,
            'description': final_content,
            'full_content': full_article_text if full_article_text else description,
            'image_url': image_url,
            'title': title,
            'link': current_url
        }

    except Exception as e:
        print(f"     Error extracting metadata: {e}")
        return {
            'date_published': None,
            'description': "",
            'full_content': "",
            'image_url': "",
            'title': "",
            'link': driver.current_url if driver else ""
        }

def is_article_in_date_range(article_date, start_date, end_date):
    """Check if article date falls within the specified range."""
    if not article_date:
        return False  # Exclude articles without parseable dates for accuracy
    
    # Convert to date only for comparison
    article_date = article_date.date()
    return start_date <= article_date <= end_date

def get_articles_from_xpath_page(driver, url, page_name="page", start_date=None, end_date=None):
    """Scrape articles from FT.lk page using provided XPaths (works for top-story and business-news)."""
    print(f"\n[INFO] Processing {page_name} articles from: {url}")

    # Navigate to page
    driver.get(url)

    try:
        # Wait for the page to load
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        time.sleep(3)  # Additional wait for dynamic content
        print(f"   {page_name.capitalize()} page loaded successfully")
    except Exception as e:
        print(f"   Timeout waiting for page: {e}")
        return [], 0, 0

    articles_found = []
    articles_processed = 0
    articles_in_range = 0
    articles_outside_range = 0

    # Scroll down to load all articles
    print(f"   Scrolling page to load all articles...")
    last_height = driver.execute_script("return document.body.scrollHeight")
    scroll_attempts = 0
    max_scroll_attempts = 10
    
    while scroll_attempts < max_scroll_attempts:
        # Scroll down to bottom
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)  # Wait for content to load
        
        # Calculate new scroll height and compare with last scroll height
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            # No more content loaded, break
            break
        last_height = new_height
        scroll_attempts += 1
    
    print(f"   Scrolled {scroll_attempts} times to load all content")
    
    # Scroll back to top
    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(1)

    # Dynamically find all article links using XPath pattern
    # Different pages use different div indices: div[2] for top-story, div[3] for business-news
    print(f"   Finding all article links...")
    
    heading_xpaths = []
    # Try indices from 1 to 50 to find all articles
    for i in range(1, 51):
        # Try both div[2] and div[3], and both patterns: div/a and div[2]/a
        for div_idx in [2, 3]:
            for pattern in [f"/html/body/header/div[2]/div[2]/div[{div_idx}]/div[1]/div[1]/div/header/div/div/div/div[{i}]/div/a",
                            f"/html/body/header/div[2]/div[2]/div[{div_idx}]/div[1]/div[1]/div/header/div/div/div/div[{i}]/div[2]/a"]:
                try:
                    element = driver.find_element(By.XPATH, pattern)
                    # Check if this XPath is already in our list (avoid duplicates)
                    if pattern not in heading_xpaths:
                        heading_xpaths.append(pattern)
                except:
                    continue
    
    # If XPath method didn't work well, try finding all h3 elements with the heading pattern
    if not heading_xpaths or len(heading_xpaths) < 4:
        print(f"   XPath method found {len(heading_xpaths)} articles, trying alternative method...")
        try:
            # Find all h3 elements that are children of article links
            soup = BeautifulSoup(driver.page_source, 'html.parser')
            # Try both div[2] and div[3] patterns
            for div_idx in [2, 3]:
                base_xpath = f"/html/body/header/div[2]/div[2]/div[{div_idx}]/div[1]/div[1]/div/header/div/div/div/div"
                # Try to find all div elements under the base path
                for i in range(1, 51):
                    try:
                        # Try to find any link with h3 inside
                        test_xpath = f"{base_xpath}[{i}]//a[h3]"
                        elements = driver.find_elements(By.XPATH, test_xpath)
                        for elem in elements:
                            # Get the XPath of this element's parent structure
                            # We'll use a more direct approach - get href and reconstruct
                            href = elem.get_attribute('href')
                            if href and '/top-story/' in href or '/business-news/' in href or any(x in href for x in ['/news/', '/front-page/']):
                                # Find the full XPath by trying patterns
                                for p in [f"{base_xpath}[{i}]/div/a", f"{base_xpath}[{i}]/div[2]/a"]:
                                    try:
                                        test_elem = driver.find_element(By.XPATH, p)
                                        if test_elem == elem and p not in heading_xpaths:
                                            heading_xpaths.append(p)
                                            break
                                    except:
                                        continue
                    except:
                        continue
        except Exception as e:
            print(f"   Alternative method error: {e}")

    # Fallback: use the original 4 XPaths if dynamic search didn't work
    # Try both div[2] (top-story) and div[3] (business-news) patterns
    if not heading_xpaths:
        print(f"   Using fallback: trying both div[2] and div[3] XPath patterns")
        for div_idx in [2, 3]:
            fallback_patterns = [
                f"/html/body/header/div[2]/div[2]/div[{div_idx}]/div[1]/div[1]/div/header/div/div/div/div[1]/div/a",
                f"/html/body/header/div[2]/div[2]/div[{div_idx}]/div[1]/div[1]/div/header/div/div/div/div[2]/div[2]/a",
                f"/html/body/header/div[2]/div[2]/div[{div_idx}]/div[1]/div[1]/div/header/div/div/div/div[3]/div/a",
                f"/html/body/header/div[2]/div[2]/div[{div_idx}]/div[1]/div[1]/div/header/div/div/div/div[4]/div[2]/a"
            ]
            # Test which pattern works
            for pattern in fallback_patterns:
                try:
                    driver.find_element(By.XPATH, pattern)
                    heading_xpaths.append(pattern)
                except:
                    pass
            
            # If we found articles with this div index, stop trying
            if heading_xpaths:
                print(f"   Found {len(heading_xpaths)} articles using div[{div_idx}] pattern")
                break

    print(f"   Found {len(heading_xpaths)} article links to process")

    try:
        # Collect all unique article URLs first to avoid duplicates
        article_urls_seen = set()
        
        for i, heading_xpath in enumerate(heading_xpaths, 1):
            try:
                print(f"\n   Article {i}/{len(heading_xpaths)}:")
                
                # Find the heading link element
                heading_link = driver.find_element(By.XPATH, heading_xpath)
                
                # Get the heading text
                heading_h3_xpath = heading_xpath + "/h3"
                try:
                    heading_element = driver.find_element(By.XPATH, heading_h3_xpath)
                    title = heading_element.text.strip()
                    print(f"     Title: {title[:80]}...")
                except:
                    # Fallback: get text from the link itself
                    title = heading_link.text.strip()
                    print(f"     Title (from link): {title[:80]}...")

                # Get the article URL
                article_url = heading_link.get_attribute('href')
                if not article_url:
                    print(f"     [SKIP] No href found for article {i}")
                    continue
                
                if article_url.startswith('/'):
                    article_url = 'https://www.ft.lk' + article_url
                
                # Skip if we've already processed this URL
                if article_url in article_urls_seen:
                    print(f"     [SKIP] Duplicate article URL, already processed")
                    continue
                
                article_urls_seen.add(article_url)
                print(f"     Link: {article_url}")

                # Click on the heading to navigate to article
                try:
                    heading_link.click()
                    time.sleep(3)  # Wait for article page to load
                except Exception as e:
                    # If click fails, try navigating directly
                    print(f"     Click failed, navigating directly: {e}")
                    driver.get(article_url)
                    time.sleep(3)

                # Extract detailed metadata from the article page
                metadata = extract_article_metadata(driver)

                # Check date range if dates are provided
                article_date = metadata['date_published']
                should_include = True
                
                if start_date and end_date:
                    if article_date is None:
                        print(f"     No date found, including article")
                    elif is_article_in_date_range(article_date, start_date, end_date):
                        print(f"     Article is in date range!")
                        articles_in_range += 1
                        should_include = True
                    else:
                        article_date_str = article_date.strftime('%Y-%m-%d') if article_date else "Unknown"
                        print(f"     [SKIP] Article outside date range: {article_date_str}")
                        articles_outside_range += 1
                        should_include = False
                else:
                    # No date filtering, include all articles
                    should_include = True

                if should_include:
                    # Use metadata from article page
                    final_title = metadata['title'] if metadata['title'] else title
                    final_date = metadata['date_published']
                    final_image = metadata['image_url']
                    final_description = metadata.get('full_content') or metadata['description']

                    # Standardized date format
                    if final_date:
                        standardized_date = final_date.strftime("%Y-%m-%d %H:%M:%S")
                    else:
                        standardized_date = "Unknown"

                    articles_found.append({
                        'title': final_title,
                        'link': article_url,
                        'summary': final_description,
                        'date': standardized_date,
                        'image_url': final_image,
                        'date_source': f"Article page: {standardized_date}"
                    })
                    articles_processed += 1

                # Navigate back to the list page
                print(f"     [BACK] Going back to {page_name} page...")
                driver.get(url)
                time.sleep(2)  # Wait for list page to reload

            except Exception as e:
                print(f"     Error processing article {i}: {e}")
                # Try to go back to list page
                try:
                    driver.get(url)
                    time.sleep(2)
                except Exception as e2:
                    print(f"     Could not return to {page_name} page: {e2}")
                continue

    except Exception as e:
        print(f"     Error finding articles: {e}")
        return [], 0, 0

    print(f"\n   Page summary: {articles_processed} processed, {articles_in_range} in range, {articles_outside_range} outside range")
    return articles_found, articles_in_range, articles_outside_range

def get_articles_from_page(driver, url, start_date, end_date):
    """Process articles from a single FT.lk list page using CSS selectors."""
    print(f"\n[INFO] Processing articles from: {url}")

    # Navigate to list page
    driver.get(url)

    try:
        # Wait for the page to load
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
        time.sleep(3)  # Additional wait for dynamic content
        print(f"   List page loaded successfully")
    except Exception as e:
        print(f"   Timeout waiting for page: {e}")
        return [], 0, 0

    articles_found = []
    articles_in_range = 0
    articles_outside_range = 0
    consecutive_outside_range = 0
    max_consecutive_outside = 2

    print(f"   Scanning for article links...")

    # Find all article links using BeautifulSoup (more flexible than XPath)
    try:
        soup = BeautifulSoup(driver.page_source, 'html.parser')

        # Look for article divs with class 'lineg' or find all links with /news/ or /front-page/ in them
        article_links = []

        # Try finding divs with class 'lineg'
        article_divs = soup.find_all('div', class_='lineg')
        if article_divs:
            print(f"   Found {len(article_divs)} article divs")
            for div in article_divs:
                h3_tag = div.find('h3', class_='newsch')
                if h3_tag:
                    link_tag = h3_tag.find_parent('a', href=True)
                    if link_tag:
                        title = h3_tag.get_text(strip=True)
                        link = link_tag['href']
                        if link.startswith('/'):
                            link = 'https://www.ft.lk' + link
                        article_links.append({'title': title, 'link': link})

        # Fallback: find all links in /news/ or /front-page/
        if not article_links:
            print(f"   No 'lineg' divs found, trying to find links by pattern...")
            all_links = soup.find_all('a', href=True)
            for link_tag in all_links:
                href = link_tag['href']
                if ('/news/' in href or '/front-page/' in href) and href.count('/') >= 3:
                    title = link_tag.get_text(strip=True)
                    if title and len(title) > 10:  # Reasonable title length
                        if href.startswith('/'):
                            href = 'https://www.ft.lk' + href
                        # Avoid duplicates
                        if not any(a['link'] == href for a in article_links):
                            article_links.append({'title': title, 'link': href})

        print(f"   Found {len(article_links)} article links to process")

        if not article_links:
            print(f"   No articles found on this page")
            return [], 0, 0

        # Process each article
        for i, article in enumerate(article_links, 1):
            title = article['title']
            article_link = article['link']

            print(f"\n   Article {i}/{len(article_links)}: {title[:60]}...")
            print(f"     Link: {article_link}")

            # Navigate to article page
            try:
                driver.get(article_link)
                time.sleep(3)  # Wait for article page to load

                # Extract detailed metadata from the article page
                metadata = extract_article_metadata(driver)

                # Check if article is in date range
                article_date = metadata['date_published']

                if article_date is None:
                    print(f"     No date found, skipping article")
                    consecutive_outside_range += 1
                elif is_article_in_date_range(article_date, start_date, end_date):
                    print(f"     Article is in date range!")

                    # Use metadata from article page
                    final_title = metadata['title'] if metadata['title'] else title
                    final_date = metadata['date_published']
                    final_image = metadata['image_url']
                    final_description = metadata.get('full_content') or metadata['description']

                    # Standardized date format
                    standardized_date = final_date.strftime("%Y-%m-%d %H:%M:%S")

                    articles_found.append({
                        'title': final_title,
                        'link': article_link,
                        'summary': final_description,
                        'date': standardized_date,
                        'image_url': final_image,
                        'date_source': f"Article page: {standardized_date}"
                    })
                    articles_in_range += 1
                    consecutive_outside_range = 0  # Reset counter
                else:
                    article_date_str = article_date.strftime('%Y-%m-%d')
                    print(f"     [SKIP] Article outside date range: {article_date_str}")
                    articles_outside_range += 1
                    consecutive_outside_range += 1

                    # Check if article is significantly older than target range
                    days_before_range = (start_date - article_date.date()).days
                    if days_before_range > 2:
                        print(f"     Article is {days_before_range} days before target range - stopping immediately")
                        break

                # Navigate back to the list page
                print(f"     [BACK] Going back to list page...")
                driver.get(url)
                time.sleep(2)  # Wait for list page to reload

            except Exception as e:
                print(f"     Error processing article: {e}")
                # Try to go back to list page
                try:
                    driver.get(url)
                    time.sleep(2)
                except Exception as e2:
                    print(f"     Could not return to list page: {e2}")
                consecutive_outside_range += 1

            # Check if we should stop (too many consecutive articles outside range)
            if consecutive_outside_range >= max_consecutive_outside:
                print(f"\n   Stopping: {max_consecutive_outside} consecutive articles outside date range")
                break

    except Exception as e:
        print(f"     Error finding articles: {e}")
        return [], 0, 0

    print(f"\n   Page summary: {articles_in_range} in range, {articles_outside_range} outside range")
    return articles_found, articles_in_range, articles_outside_range

def main_incremental():
    """Scrape first page of each category, stop when the last-scraped article URL is detected."""
    json_filename = _data_json_path()
    checkpoint_link, checkpoint_title = get_last_scraped_checkpoint(json_filename)
    bootstrap = not checkpoint_link
    bootstrap_limit = 40

    print("[INCREMENTAL] FT.lk — stop when last scraped article is detected")
    if bootstrap:
        print(f"[INCREMENTAL] No prior data; bootstrap (max {bootstrap_limit} articles)")

    driver = _create_driver(headless=True)

    categories = [
        ("business-news", "34"),
        ("news", "44"),
        ("news", "56"),
        ("opinion", "14"),
        ("columns", "18"),
        ("sports", "23"),
        ("travel-tourism", "27"),
    ]

    new_articles = []
    seen_this_run = set()
    stop_all = False

    try:
        for cat_name, cat_id in categories:
            if stop_all:
                break
            page_url = f"https://www.ft.lk/{cat_name}/{cat_id}"
            print(f"\n{'=' * 60}")
            print(f"[INCREMENTAL] Category: {cat_name}/{cat_id}")
            print(f"{'=' * 60}")

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

                print(f"\n[INFO] New article {i}: {entry['title'][:60]}...")
                try:
                    driver.get(link)
                    time.sleep(2)
                    metadata = extract_article_metadata(driver)
                except Exception as e:
                    print(f"[ERROR] Failed to fetch article: {e}")
                    continue

                article_date = metadata['date_published']
                standardized_date = (
                    article_date.strftime("%Y-%m-%d %H:%M:%S")
                    if article_date
                    else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                )
                final_description = metadata.get('full_content') or metadata['description']

                new_articles.append({
                    'title': metadata['title'] or entry['title'],
                    'link': link,
                    'summary': final_description,
                    'date': standardized_date,
                    'image_url': metadata['image_url'],
                    'date_source': f"Article page: {standardized_date}",
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
    print("\n[INFO] FT.lk incremental scraper finished.")


def main(start_date=None, end_date=None, scrape_mode="all"):
    """Enhanced main function with date range filtering.
    
    Args:
        start_date: Start date for filtering articles
        end_date: End date for filtering articles
        scrape_mode: "all" (default - scrapes all 3 pages)
    """
    
    # Default to yesterday and today if no dates provided
    if not start_date or not end_date:
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=1)
        print(f"[DATE] No date range provided, using default: {start_date} to {end_date}")
    else:
        print(f"[DATE] Scraping articles from {start_date} to {end_date}")
    
    print(f"[INFO] Scrape mode: {scrape_mode}")
    print("[INFO] Starting FT.lk scraper...")
    print("=" * 50)

    driver = _create_driver(headless=False)

    all_articles = []
    total_articles_in_range = 0
    total_articles_outside_range = 0
    
    categories = [
        ("business-news", "34"),
        ("news", "44"),
        ("news", "56"),
        ("opinion", "14"),
        ("columns", "18"),
        ("sports", "23"),
        ("travel-tourism", "27")
    ]
    scraped_urls = set()
    
    try:
        for cat_name, cat_id in categories:
            cat_url = f"https://www.ft.lk/{cat_name}/{cat_id}"
            print(f"\n{'='*60}")
            print(f"[INFO] Scraping category: {cat_name}/{cat_id} ({cat_url})")
            print(f"{'='*60}")
            
            consecutive_empty_pages = 0
            max_consecutive_empty = 3
            max_pages = 8
            
            for page_num in range(max_pages):
                if page_num == 0:
                    url = cat_url
                else:
                    url = f"{cat_url}/{page_num * 30}"
                
                print(f"\n[INFO] {cat_name} Page {page_num + 1}:")
                
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
                    
                    if page_in_range == 0:
                        consecutive_empty_pages += 1
                        print(f"  [WARNING] No articles in range on this page ({consecutive_empty_pages}/{max_consecutive_empty} consecutive)")
                        if consecutive_empty_pages >= max_consecutive_empty:
                            print(f"  [ERROR] Stopping category {cat_name}: {max_consecutive_empty} consecutive pages with no articles in date range")
                            break
                    else:
                        consecutive_empty_pages = 0
                    
                    time.sleep(1)
                    
                except Exception as e:
                    print(f"  [ERROR] Error processing {cat_name} page {page_num + 1}: {e}")
                    continue
        
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

    json_filename = _data_json_path()
    os.makedirs(os.path.dirname(json_filename), exist_ok=True)

    try:
        if not all_articles:
            print(f" [INFO] 0 articles scraped. Preserving existing data in {json_filename} intact.")
        else:
            with open(json_filename, 'w', encoding='utf-8') as jsonfile:
                json.dump(all_articles, jsonfile, ensure_ascii=False, indent=2)

        if not all_articles:
            print(f"[WARNING] No articles found in the specified date range ({start_date} to {end_date})")
            print(f"[INFO] Saved empty JSON array to {json_filename}")
            print("[INFO] Try expanding your date range or check the website for recent articles")
        else:
            print(f"\n[INFO] Successfully saved {len(all_articles)} articles to {json_filename}")
            print(f"[INFO] File location: {os.path.abspath(json_filename)}")
            print(f"[INFO] Date range: {start_date} to {end_date}")

    except Exception as e:
        print(f"[ERROR] Error saving to file: {e}")

    print("\n[INFO] FT.lk Enhanced Scraper completed successfully!")

if __name__ == "__main__":
    if is_incremental_mode():
        main_incremental()
        sys.exit(0)

    date_args = [a for a in sys.argv[1:] if not a.startswith("--")]
    start_date = None
    end_date = None

    if len(date_args) >= 2:
        try:
            start_date = datetime.strptime(date_args[0], "%Y-%m-%d").date()
            end_date = datetime.strptime(date_args[1], "%Y-%m-%d").date()
        except ValueError as e:
            print(f"[ERROR] Invalid date format. Use YYYY-MM-DD. Error: {e}")
            print("[INFO] Example: python ftlk_selenium_json.py 2025-01-18 2025-01-19")
            sys.exit(1)

    main(start_date, end_date)