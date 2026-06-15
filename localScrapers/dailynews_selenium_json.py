try:
    import undetected_chromedriver as uc
    USE_UNDETECTED = True
    print("[INFO] undetected-chromedriver imported successfully")
except ImportError:
    USE_UNDETECTED = False
    print("[WARNING] undetected-chromedriver not available, using regular Selenium")

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

# Force unbuffered output
sys.stdout.reconfigure(line_buffering=True) if hasattr(sys.stdout, 'reconfigure') else None

def extract_article_metadata(driver):
    """Extract detailed metadata from current Daily News article page."""
    try:
        # Wait for page to load
        time.sleep(2)

        soup = BeautifulSoup(driver.page_source, 'html.parser')

        # Extract date - try multiple patterns
        date_published = None
        date_xpaths = [
            "/html/body/div[1]/div[3]/div[1]/div/article/div[2]/div[2]/div[2]/span/time",
            "//time",
            "//span[@class='date']//time",
            "//time[@datetime]"
        ]

        for date_xpath in date_xpaths:
            try:
                date_element = driver.find_element(By.XPATH, date_xpath)
                date_text = date_element.text.strip()
                # Also try datetime attribute
                datetime_attr = date_element.get_attribute('datetime')
                if datetime_attr:
                    date_text = datetime_attr
                
                if date_text:
                    print(f"     Found date text: {date_text}")

                    # Try to parse the date
                    date_formats = [
                        "%Y-%m-%d",               # 2026-01-19
                        "%B %d, %Y",              # January 19, 2026
                        "%b %d, %Y",              # Jan 19, 2026
                        "%d %B %Y",               # 19 January 2026
                        "%d %b %Y",               # 19 Jan 2026
                        "%A, %d %B %Y",           # Monday, 19 January 2026
                        "%A, %d %b %Y",           # Monday, 19 Jan 2026
                        "%Y-%m-%dT%H:%M:%S",      # ISO format
                        "%Y-%m-%d %H:%M:%S",      # 2026-01-19 12:00:00
                    ]

                    for fmt in date_formats:
                        try:
                            date_published = datetime.strptime(date_text, fmt)
                            print(f"     Parsed date: {date_published.strftime('%Y-%m-%d %H:%M:%S')}")
                            break
                        except:
                            continue
                    
                    if date_published:
                        break
            except:
                continue

        # Extract image
        image_url = ""
        image_xpaths = [
            "/html/body/div[1]/div[3]/div[1]/div/article/div[1]/img",
            "//article//img[@src]",
            "//div[@class='post-thumbnail']//img",
            "//img[contains(@class, 'featured')]"
        ]
        
        for image_xpath in image_xpaths:
            try:
                image_element = driver.find_element(By.XPATH, image_xpath)
                image_url = image_element.get_attribute('src')
                if image_url:
                    print(f"     Found image: {image_url[:60]}...")
                    break
            except:
                continue

        # Extract full article content
        full_article_text = ""
        article_content_xpaths = [
            "/html/body/div[1]/div[3]/div[1]/div/article/div[2]/div[3]",
            "/html/body/div[1]/div[3]/div[1]/div/article/div[2]",
            "//article//div[contains(@class, 'entry-content')]",
            "//article//div[contains(@class, 'post-content')]",
            "//article//div[contains(@class, 'content')]",
            "/html/body/div[1]/div[3]/div[1]/div/article"
        ]
        
        for xpath in article_content_xpaths:
            try:
                content_element = driver.find_element(By.XPATH, xpath)
                # Try to get paragraphs within the content
                paragraphs = content_element.find_elements(By.TAG_NAME, 'p')
                if paragraphs:
                    full_article_text = '\n\n'.join([p.text.strip() for p in paragraphs if p.text.strip() and len(p.text.strip()) > 20])
                    if full_article_text and len(full_article_text) > 100:
                        print(f"     Found article content: {len(full_article_text)} characters")
                        break
            except:
                continue
        
        # If paragraph extraction didn't work, try getting all text
        if not full_article_text or len(full_article_text) < 100:
            for xpath in article_content_xpaths:
                try:
                    content_element = driver.find_element(By.XPATH, xpath)
                    full_text = content_element.text.strip()
                    if full_text and len(full_text) > 100:
                        full_article_text = full_text
                        print(f"     Extracted full text from div: {len(full_article_text)} characters")
                        break
                except:
                    continue

        # Extract title from OpenGraph meta tag or page
        title = ""
        og_title = soup.find('meta', attrs={'property': 'og:title'})
        if og_title and og_title.has_attr('content'):
            title = og_title['content']
        else:
            # Try h1 tag
            try:
                h1 = driver.find_element(By.TAG_NAME, 'h1')
                title = h1.text.strip()
            except:
                pass

        # Get current URL
        current_url = driver.current_url

        return {
            'date_published': date_published,
            'full_content': full_article_text,
            'description': full_article_text if full_article_text else "",
            'image_url': image_url,
            'title': title,
            'link': current_url
        }

    except Exception as e:
        print(f"     Error extracting metadata: {e}")
        import traceback
        traceback.print_exc()
        return {
            'date_published': None,
            'full_content': "",
            'description': "",
            'image_url': "",
            'title': "",
            'link': driver.current_url if driver else ""
        }

def is_article_in_date_range(article_date, start_date, end_date):
    """Check if article date falls within the specified range."""
    if not article_date:
        return False
    
    # Convert to date only for comparison
    article_date = article_date.date()
    return start_date <= article_date <= end_date

def get_articles_from_category_page(driver, url, start_date=None, end_date=None, existing_urls=None):
    """Scrape articles from Daily News category page using provided XPaths.
    
    Args:
        driver: Selenium WebDriver instance
        url: URL of the category page to scrape
        start_date: Start date for filtering articles
        end_date: End date for filtering articles
        existing_urls: Optional set of URLs already scraped (to avoid duplicates across categories)
    """
    print(f"\n[INFO] Processing Daily News category articles from: {url}")

    # Navigate to page - already done in main, but check current URL
    if driver.current_url != url:
        driver.get(url)
        try:
            # Wait for the page to load
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            time.sleep(3)  # Additional wait for dynamic content
            print(f"   Category page loaded successfully")
        except Exception as e:
            print(f"   Timeout waiting for page: {e}")
            return [], 0, 0

    all_articles_found = []
    all_scraped_urls = existing_urls if existing_urls is not None else set()  # Track URLs we've already scraped
    total_articles_processed = 0
    total_articles_in_range = 0
    total_articles_outside_range = 0
    
    page_num = 1
    max_pages = 20  # Safety limit
    consecutive_pages_outside_range = 0
    max_consecutive_outside = 2  # Stop if 2 consecutive pages have no articles in range
    
    # Base URL for pagination - remove trailing slash and any existing page number
    base_url = url.rstrip('/')
    if '/page/' in base_url:
        base_url = base_url.split('/page/')[0]
    
    while page_num <= max_pages:
        # Construct page URL using pattern
        if page_num == 1:
            page_url = base_url + "/"
        else:
            page_url = f"{base_url}/page/{page_num}/"
        
        print(f"\n[INFO] === Processing page {page_num} ===")
        print(f"   Page URL: {page_url}")
        
        # Navigate to the page
        print(f"   Navigating to page {page_num}...")
        driver.get(page_url)
        time.sleep(4)  # Wait for page to load
        
        # Scroll down to make sure all articles are visible
        print(f"   Scrolling to load all articles on current page...")
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(1)

        # Dynamically find all article links using XPath pattern
        # Pattern: /html/body/div[1]/div[3]/div[1]/div/ul/li[N]/article/div[2]/div[1]/h2/a
        # Date Pattern: /html/body/div[1]/div[3]/div[1]/div/ul/li[N]/article/div[2]/div[1]/div[2]/span/time
        print(f"   Finding all article links and dates from current page...")
        
        # Collect all article URLs, titles, and dates from this page
        article_data = []  # List of dicts with url, title, date, xpath
        # Try indices from 1 to 100 to find all articles on current page
        for i in range(1, 101):
            heading_xpath = f"/html/body/div[1]/div[3]/div[1]/div/ul/li[{i}]/article/div[2]/div[1]/h2/a"
            date_xpath = f"/html/body/div[1]/div[3]/div[1]/div/ul/li[{i}]/article/div[2]/div[1]/div[2]/span/time"
            
            try:
                element = driver.find_element(By.XPATH, heading_xpath)
                # Verify it has an href
                href = element.get_attribute('href')
                if href:
                    if href.startswith('/'):
                        href = 'https://dailynews.lk' + href
                    
                    # Get title
                    try:
                        title = element.text.strip()
                    except:
                        title = ""
                    
                    # Get date from list page - try multiple methods
                    date_text = None
                    
                    # Method 1: Try the time element XPath
                    try:
                        date_element = driver.find_element(By.XPATH, date_xpath)
                        # First try text
                        date_text = date_element.text.strip()
                        if date_text:
                            print(f"      [DATE] Found from time element: '{date_text}'")
                        # If empty, try datetime attribute
                        if not date_text:
                            datetime_attr = date_element.get_attribute('datetime')
                            if datetime_attr:
                                date_text = datetime_attr
                                print(f"      [DATE] Found from datetime attr: '{date_text}'")
                    except:
                        pass
                    
                    # Method 2: Try the span containing the date
                    if not date_text:
                        try:
                            span_xpath = f"/html/body/div[1]/div[3]/div[1]/div/ul/li[{i}]/article/div[2]/div[1]/div[2]/span"
                            span_element = driver.find_element(By.XPATH, span_xpath)
                            date_text = span_element.text.strip()
                            if date_text:
                                print(f"      [DATE] Found from span: '{date_text}'")
                        except:
                            pass
                    
                    # Method 3: Try finding any time element within the article
                    if not date_text:
                        try:
                            article_xpath = f"/html/body/div[1]/div[3]/div[1]/div/ul/li[{i}]/article"
                            article_element = driver.find_element(By.XPATH, article_xpath)
                            time_elements = article_element.find_elements(By.TAG_NAME, 'time')
                            for time_el in time_elements:
                                date_text = time_el.text.strip() or time_el.get_attribute('datetime')
                                if date_text:
                                    print(f"      [DATE] Found from article time tag: '{date_text}'")
                                    break
                        except:
                            pass
                    
                    if not date_text:
                        print(f"      [DATE] No date found for article {i}")
                    
                    # Avoid duplicates by URL (check against URLs we've already scraped)
                    if href in all_scraped_urls:
                        print(f"      [SKIP] Already scraped: {href[:60]}...")
                    else:
                        print(f"      [NEW] Article {i}: {title[:40]}... | URL: {href[:50]}...")
                        article_data.append({
                            'url': href,
                            'title': title,
                            'date_text': date_text,
                            'xpath': heading_xpath
                        })
            except:
                # No more articles found on this page
                continue
        
        # If we found no new articles on this page, stop
        if not article_data:
            print(f"   No new articles found on page {page_num}")
            break

        print(f"   Found {len(article_data)} article links on page {page_num}")

        # Filter articles by date on the list page BEFORE clicking
        page_in_range_count = 0
        page_outside_range_count = 0
        articles_to_process = []  # Only articles in range
        
        print(f"\n   Filtering articles by date range: {start_date} to {end_date}")
        
        for article_info in article_data:
            date_text = article_info.get('date_text', '')
            title = article_info.get('title', '')[:50]
            
            # Parse date from list page
            article_date = None
            if date_text:
                date_formats = [
                    "%B %d, %Y",              # January 19, 2026
                    "%b %d, %Y",              # Jan 19, 2026
                    "%Y-%m-%d",               # 2026-01-19
                    "%d %B %Y",               # 19 January 2026
                    "%d %b %Y",               # 19 Jan 2026
                    "%A, %d %B %Y",           # Monday, 19 January 2026
                    "%A, %d %b %Y",           # Monday, 19 Jan 2026
                    "%Y-%m-%dT%H:%M:%S",      # ISO format
                    "%Y-%m-%d %H:%M:%S",      # 2026-01-19 12:00:00
                ]
                
                for fmt in date_formats:
                    try:
                        article_date = datetime.strptime(date_text, fmt)
                        print(f"     '{title}...' - Date: {article_date.strftime('%Y-%m-%d')}")
                        break
                    except:
                        continue
                
                if not article_date:
                    print(f"     '{title}...' - Could not parse date: '{date_text}'")
            else:
                print(f"     '{title}...' - No date found")
            
            # Store parsed date in article_info for later use
            article_info['parsed_date'] = article_date
            
            # Check if in date range
            if start_date and end_date:
                if article_date:
                    if is_article_in_date_range(article_date, start_date, end_date):
                        page_in_range_count += 1
                        articles_to_process.append(article_info)
                        print(f"       -> IN RANGE, will process")
                    else:
                        page_outside_range_count += 1
                        print(f"       -> OUTSIDE RANGE, skipping")
                else:
                    # No date found - still include it (we'll try to get date from article page)
                    articles_to_process.append(article_info)
                    print(f"       -> NO DATE, including anyway")
            else:
                # No date filtering
                articles_to_process.append(article_info)
                print(f"       -> INCLUDED (no date filter)")
        
        print(f"\n   === Page {page_num} Summary ===")
        print(f"   Total articles found on page: {len(article_data)}")
        print(f"   Articles in date range: {page_in_range_count}")
        print(f"   Articles outside date range: {page_outside_range_count}")
        print(f"   Articles to process (incl. no-date): {len(articles_to_process)}")
        
        # If no articles to process on this page
        if not articles_to_process:
            print(f"   No articles to process on page {page_num}")
            # Still try to go to next page if there were articles outside range
            if page_outside_range_count > 0:
                print(f"   All articles on this page are outside date range, moving to next page...")
            else:
                print(f"   No articles found at all, stopping")
                break
        
        # If no articles in range on this page, increment counter
        if page_in_range_count == 0:
            consecutive_pages_outside_range += 1
            if consecutive_pages_outside_range >= max_consecutive_outside:
                print(f"   Stopping: {max_consecutive_outside} consecutive pages with no articles in date range")
                break
        else:
            consecutive_pages_outside_range = 0  # Reset
        
        try:
            # Process only articles in range
            for i, article_info in enumerate(articles_to_process, 1):
                try:
                    article_url = article_info['url']
                    title = article_info.get('title', '')
                    date_text = article_info.get('date_text', '')
                    
                    print(f"\n   Article {i}/{len(articles_to_process)}:")
                    print(f"     Title: {title[:80]}..." if title else "     Title: (will extract from article)")
                    print(f"     Date from list: {date_text}" if date_text else "     Date: Not found on list page")
                    print(f"     Link: {article_url}")
                    
                    # Parse date from list page
                    article_date = None
                    if date_text:
                        date_formats = [
                            "%Y-%m-%d",               # 2026-01-19
                            "%B %d, %Y",              # January 19, 2026
                            "%b %d, %Y",              # Jan 19, 2026
                            "%d %B %Y",               # 19 January 2026
                            "%d %b %Y",               # 19 Jan 2026
                            "%A, %d %B %Y",           # Monday, 19 January 2026
                            "%A, %d %b %Y",           # Monday, 19 Jan 2026
                            "%Y-%m-%dT%H:%M:%S",      # ISO format
                            "%Y-%m-%d %H:%M:%S",      # 2026-01-19 12:00:00
                        ]
                        
                        for fmt in date_formats:
                            try:
                                article_date = datetime.strptime(date_text, fmt)
                                print(f"     Parsed date: {article_date.strftime('%Y-%m-%d')}")
                                break
                            except:
                                continue

                    # Navigate directly to article URL
                    driver.get(article_url)
                    time.sleep(3)  # Wait for article page to load

                    # Extract detailed metadata from the article page
                    metadata = extract_article_metadata(driver)
                    
                    # Use date from list page if article page didn't have one
                    if not metadata['date_published'] and article_date:
                        metadata['date_published'] = article_date
                        print(f"     Using date from list page: {article_date.strftime('%Y-%m-%d')}")

                    # Check date range if dates are provided
                    article_date = metadata['date_published']
                    should_include = True
                    
                    if start_date and end_date:
                        if article_date is None:
                            print(f"     No date found, including article")
                        elif is_article_in_date_range(article_date, start_date, end_date):
                            print(f"     Article is in date range!")
                            total_articles_in_range += 1
                            should_include = True
                        else:
                            article_date_str = article_date.strftime('%Y-%m-%d') if article_date else "Unknown"
                            print(f"     [SKIP] Article outside date range: {article_date_str}")
                            total_articles_outside_range += 1
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

                        article_entry = {
                            'title': final_title,
                            'link': article_url,
                            'summary': final_description,
                            'date': standardized_date,
                            'image_url': final_image,
                            'date_source': f"Article page: {standardized_date}"
                        }
                        all_articles_found.append(article_entry)
                        all_scraped_urls.add(article_url)  # Track scraped URL
                        total_articles_processed += 1

                except Exception as e:
                    print(f"     Error processing article {i}: {e}")
                import traceback
                traceback.print_exc()
                # Continue to next article
                continue

        except Exception as e:
            print(f"     Error processing articles on page {page_num}: {e}")
            import traceback
            traceback.print_exc()
        
        # Move to next page using URL pattern
        page_num += 1
        print(f"\n   Moving to page {page_num}...")

    print(f"\n=== Overall summary: {total_articles_processed} articles processed across {page_num} pages ===")
    return all_articles_found, total_articles_in_range, total_articles_outside_range

def main(start_date=None, end_date=None, categories=None):
    """Main function for Daily News scraper.
    
    Args:
        start_date: Start date for filtering articles
        end_date: End date for filtering articles
        categories: List of categories to scrape (e.g., ['local', 'politics']). 
                   If None, scrapes all available categories.
    """
    
    # Default to yesterday and today if no dates provided
    if not start_date or not end_date:
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=1)
        print(f"[DATE] No date range provided, using default: {start_date} to {end_date}")
    else:
        print(f"[DATE] Scraping articles from {start_date} to {end_date}")
    
    # Default categories to scrape
    if categories is None:
        categories = ['local', 'politics', 'business', 'lawnorder', 'world', 'sports', 'editorial', 'features']
    elif isinstance(categories, str):
        categories = [categories]  # Convert single string to list
    
    print(f"[INFO] Categories to scrape: {', '.join(categories)}")
    
    print("[INFO] Starting Daily News scraper...")
    print("=" * 50)
    
    chrome_options = Options()
    chrome_options.page_load_strategy = 'eager'
    # chrome_options.add_argument("--headless")  # Disabled - showing browser window
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    # Use Chrome profile to bypass Cloudflare
    # IMPORTANT: Close all Chrome instances before running!
    import getpass
    username = getpass.getuser()
    chrome_user_data = os.path.join("C:\\Users", username, "AppData\\Local\\Google\\Chrome\\User Data")
    default_profile = os.path.join(chrome_user_data, "Default")
    
    # Use undetected-chromedriver with profile to bypass Cloudflare
    print(f"[INFO] Using undetected-chromedriver to bypass Cloudflare...", flush=True)
    
    if USE_UNDETECTED:
        options = uc.ChromeOptions()
        options.page_load_strategy = 'eager'
        # Don't use headless - Cloudflare can detect it
        
        # Note: undetected-chromedriver works better without user profile
        # It creates its own temporary profile with anti-detection features
        print(f"[INFO] Using undetected-chromedriver (creates its own profile)...", flush=True)
        
        print(f"[INFO] Starting undetected Chrome...", flush=True)
        try:
            # Let undetected-chromedriver auto-detect Chrome version
            driver = uc.Chrome(options=options, use_subprocess=False)
            print("[INFO] Undetected Chrome browser started successfully")
        except Exception as e:
            error_msg = str(e)
            print(f"[WARNING] Initial attempt failed: {error_msg}")

            # Try to extract the installed Chrome major version from the error message
            match = re.search(r"Current browser version is (\d+)", error_msg)
            if not match:
                match = re.search(r"only supports Chrome version (\d+)", error_msg)

            if match:
                major_version = int(match.group(1))
                print(f"[INFO] Mismatched chromedriver. Retrying with version_main={major_version}...")
                try:
                    options_retry = uc.ChromeOptions()
                    options_retry.page_load_strategy = 'eager'

                    driver = uc.Chrome(options=options_retry, use_subprocess=False, version_main=major_version)
                    print(f"[INFO] Undetected Chrome browser (forced version {major_version}) started successfully")
                except Exception as retry_err:
                    print(f"[WARNING] Retry with version_main={major_version} failed: {retry_err}")
                    print(f"[INFO] Trying with fresh ChromeOptions and version_main={major_version}...")
                    options_retry2 = uc.ChromeOptions()
                    options_retry2.page_load_strategy = 'eager'

                    driver = uc.Chrome(options=options_retry2, use_subprocess=False, version_main=major_version)
                    print(f"[INFO] Undetected Chrome browser (forced version {major_version}) started successfully")
            else:
                print(f"[INFO] Trying with fresh ChromeOptions...")
                options_retry2 = uc.ChromeOptions()
                options_retry2.page_load_strategy = 'eager'

                driver = uc.Chrome(options=options_retry2, use_subprocess=False)
                print(f"[INFO] Undetected Chrome browser started successfully")
    else:
        print(f"[WARNING] undetected-chromedriver not installed!", flush=True)
        print(f"[INFO] Using regular Selenium (may be blocked)", flush=True)
        
        chrome_options.add_argument("--disable-web-security")
        chrome_options.add_argument("--disable-features=VizDisplayCompositor")
        
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

    # Immediately navigate to test page to ensure driver works
    print(f"[INFO] Testing navigation...", flush=True)
    test_url = "https://dailynews.lk/category/local/"
    print(f"[INFO] Navigating to {test_url}...", flush=True)
    driver.get(test_url)
    print(f"[INFO] Navigation completed! Current URL: {driver.current_url}", flush=True)
    time.sleep(2)

    all_articles = []
    all_scraped_urls = set()  # Track URLs across all categories to avoid duplicates
    total_articles_in_range = 0
    total_articles_outside_range = 0
    
    try:
        # Scrape each category
        for category in categories:
            print(f"\n{'='*60}")
            print(f"[INFO] Scraping category: {category}")
            print(f"{'='*60}")
            
            category_url = f"https://dailynews.lk/category/{category}/"
            # Pass existing_urls to avoid processing duplicates across categories
            articles, page_in_range, page_outside_range = get_articles_from_category_page(
                driver, category_url, start_date, end_date, existing_urls=all_scraped_urls
            )
            
            all_articles.extend(articles)
            total_articles_in_range += page_in_range
            total_articles_outside_range += page_outside_range
            
            print(f"[INFO] Category '{category}': {len(articles)} articles found")
        
    finally:
        driver.quit()

    # Enhanced results summary
    print("\n" + "=" * 50)
    print("[INFO] SCRAPING SUMMARY")
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

    # Save to JSON file in the data directory
    data_dir = "data"
    os.makedirs(data_dir, exist_ok=True)
    json_filename = os.path.join(data_dir, "dailynews_latest_news.json")

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

    print("\n[INFO] Daily News scraper completed successfully!")

if __name__ == "__main__":
    from incremental import is_incremental_mode

    if is_incremental_mode():
        from incremental_outlets import run_incremental_for_module
        run_incremental_for_module(__name__)
        sys.exit(0)

    date_args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(date_args) >= 2:
        try:
            start_date = datetime.strptime(date_args[0], "%Y-%m-%d").date()
            end_date = datetime.strptime(date_args[1], "%Y-%m-%d").date()
            main(start_date, end_date)
        except ValueError as e:
            print(f"[ERROR] Invalid date format. Use YYYY-MM-DD. Error: {e}")
            print("[INFO] Example: python dailynews_selenium_json.py 2025-01-18 2025-01-19")
    else:
        main()