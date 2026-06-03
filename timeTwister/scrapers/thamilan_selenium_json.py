try:
    import undetected_chromedriver as uc  # type: ignore
    USE_UNDETECTED = True
    print("[INFO] undetected-chromedriver imported successfully")
except ImportError as e:
    USE_UNDETECTED = False
    print(f"[WARNING] undetected-chromedriver not available: {e}")
    print("[INFO] Will use regular Selenium (may be blocked by Cloudflare)")
except Exception as e:
    USE_UNDETECTED = False
    print(f"[WARNING] Error importing undetected-chromedriver: {e}")
    print("[INFO] Will use regular Selenium (may be blocked by Cloudflare)")

# Always import selenium components
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager

from bs4 import BeautifulSoup
import time
import json
import sys
# Set up UTF-8 encoding for console output (Windows fix)
if sys.platform == 'win32':
    try:
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8')
        if hasattr(sys.stderr, 'reconfigure'):
            sys.stderr.reconfigure(encoding='utf-8')
    except:
        pass

from datetime import datetime, timedelta
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import re

CATEGORY_URLS = [
    ("LOCAL", "https://www.thamilan.lk/categories/local"),
    ("NORTH", "https://www.thamilan.lk/categories/north"),
    ("EAST", "https://www.thamilan.lk/categories/east"),
    ("UPCOUNTRY", "https://www.thamilan.lk/categories/upcountry"),
    ("BUSINESS", "https://www.thamilan.lk/categories/business"),
    ("ARTICLES", "https://www.thamilan.lk/categories/article"),
    ("SPORTS", "https://www.thamilan.lk/categories/sports"),
    ("WORLD", "https://www.thamilan.lk/categories/international"),
]

def extract_article_content(driver):
    """Extract article content from Thamilan article page using BeautifulSoup only (fast)."""
    try:
        soup = BeautifulSoup(driver.page_source, 'html.parser')

        # Extract publication date from meta tag
        date_published = None
        date_meta = soup.find('meta', attrs={'property': 'article:published_time'})
        if date_meta and date_meta.has_attr('content'):
            try:
                date_str = date_meta['content']
                date_str_clean = date_str.split('+')[0].split('T')
                if len(date_str_clean) == 2:
                    date_published = datetime.strptime(f"{date_str_clean[0]} {date_str_clean[1]}", "%Y-%m-%d %H:%M:%S")
            except:
                pass

        # Extract image URL
        image_url = ""
        og_image = soup.find('meta', attrs={'property': 'og:image'})
        if og_image and og_image.has_attr('content'):
            image_url = og_image['content']

        # Extract title
        title = ""
        og_title = soup.find('meta', attrs={'property': 'og:title'})
        if og_title and og_title.has_attr('content'):
            title = og_title['content']

        # Extract article text - find all <p> tags in the main content area
        full_article_text = ""
        paragraph_texts = []

        # Thamilan uses Next.js - look for p tags with article text classes
        all_p = soup.find_all('p')
        for p in all_p:
            text = p.get_text(strip=True)
            # Filter out short/nav/date texts, keep actual paragraphs
            if text and len(text) > 40:
                paragraph_texts.append(text)

        if paragraph_texts:
            full_article_text = "\n\n".join(paragraph_texts)
            print(f"     Extracted {len(paragraph_texts)} paragraphs ({len(full_article_text)} chars)")

        return {
            'date_published': date_published,
            'image_url': image_url,
            'description': full_article_text,
            'title': title,
            'link': driver.current_url
        }

    except Exception as e:
        print(f"     [EXTRACT] Error: {e}")
        return {
            'date_published': None,
            'image_url': "",
            'description': "",
            'title': "",
            'link': driver.current_url if driver else ""
        }

def is_article_in_date_range(article_date, start_date, end_date):
    """Check if article date falls within the specified range."""
    if not article_date:
        return False
    article_date = article_date.date()
    return start_date <= article_date <= end_date

def parse_relative_date(date_text):
    """Parse relative date strings like 'a day ago', '2 days ago', '3 hours ago', etc."""
    now = datetime.now()
    text = date_text.strip().lower()
    
    # "a day ago" or "1 day ago"
    if text in ("a day ago", "1 day ago"):
        return now - timedelta(days=1)
    
    # "N days ago"
    m = re.match(r'(\d+)\s*days?\s*ago', text)
    if m:
        return now - timedelta(days=int(m.group(1)))
    
    # "an hour ago" or "1 hour ago"
    if text in ("an hour ago", "1 hour ago"):
        return now - timedelta(hours=1)
    
    # "N hours ago"
    m = re.match(r'(\d+)\s*hours?\s*ago', text)
    if m:
        return now - timedelta(hours=int(m.group(1)))
    
    # "N minutes ago"
    m = re.match(r'(\d+)\s*minutes?\s*ago', text)
    if m:
        return now - timedelta(minutes=int(m.group(1)))
    
    # "a minute ago"
    if text in ("a minute ago", "1 minute ago"):
        return now - timedelta(minutes=1)
    
    # "a week ago"
    if text in ("a week ago", "1 week ago"):
        return now - timedelta(weeks=1)
    
    # "N weeks ago"
    m = re.match(r'(\d+)\s*weeks?\s*ago', text)
    if m:
        return now - timedelta(weeks=int(m.group(1)))
    
    # "a month ago"
    if text in ("a month ago", "1 month ago"):
        return now - timedelta(days=30)
    
    # "N months ago"
    m = re.match(r'(\d+)\s*months?\s*ago', text)
    if m:
        return now - timedelta(days=int(m.group(1)) * 30)
    
    # "a year ago" or "1 year ago"
    if text in ("a year ago", "1 year ago"):
        return now - timedelta(days=365)
        
    # "N years ago"
    m = re.match(r'(\d+)\s*years?\s*ago', text)
    if m:
        return now - timedelta(days=int(m.group(1)) * 365)
    
    # Try absolute date formats
    for fmt in ["%B %d, %Y", "%d %B %Y", "%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y"]:
        try:
            return datetime.strptime(text, fmt)
        except:
            continue
    
    return None

def click_see_more(driver):
    """Click the 'See More' button once. Returns True if clicked, False if not found."""
    try:
        see_more_btn = driver.find_element(By.CSS_SELECTOR, "button.bg-grey-lighter.font-button")
        if see_more_btn.is_displayed():
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", see_more_btn)
            time.sleep(0.5)
            see_more_btn.click()
            time.sleep(2)
            return True
    except:
        pass
    # Fallback: try by text
    try:
        see_more_btn = driver.find_element(By.XPATH, "//button[contains(text(), 'See More')]")
        if see_more_btn.is_displayed():
            driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", see_more_btn)
            time.sleep(0.5)
            see_more_btn.click()
            time.sleep(2)
            return True
    except:
        pass
    return False

def get_article_cards(driver):
    """Get all article card containers using CSS selectors.
    Each card is a div containing an <a> with <h2> and a sibling <p> with date.
    Returns list of dicts with 'title', 'link', 'date_text'."""
    cards = []
    seen_links = set()
    
    # Find all the desktop h2 elements (the ones with full text, class contains 'hidden' and 'lg:block')
    h2_elements = driver.find_elements(By.CSS_SELECTOR, "h2.hidden.font-heading")
    
    for h2 in h2_elements:
        try:
            title = h2.get_attribute("textContent").strip()
            if not title:
                continue
            
            # Get parent <a> for the link
            parent_a = h2.find_element(By.XPATH, "./..")
            link = parent_a.get_attribute("href")
            if not link or link in seen_links:
                continue
            seen_links.add(link)
            
            # Get the date <p> - it's a sibling of the <a> tag, inside the same container div
            container = parent_a.find_element(By.XPATH, "./..")
            date_text = ""
            try:
                date_p = container.find_element(By.CSS_SELECTOR, "p.text-grey-base")
                date_text = date_p.get_attribute("textContent").strip()
            except:
                pass
            
            cards.append({
                'title': title,
                'link': link,
                'date_text': date_text
            })
        except:
            continue
    
    return cards

def process_articles_from_page(driver, list_url, start_date, end_date):
    """Process articles from Thamilan local page, clicking 'See More' as needed."""
    print(f"\n[INFO] Processing articles from: {list_url}")

    try:
        driver.get(list_url)
        WebDriverWait(driver, 30).until(
            lambda d: d.execute_script('return document.readyState') == 'complete'
        )
        time.sleep(3)
        print(f"[DEBUG] Page title: {driver.title}")
        print(f"[DEBUG] Current URL: {driver.current_url}")
    except Exception as e:
        print(f"[ERROR] Failed to navigate to page: {e}")
        import traceback
        traceback.print_exc()
        return [], 0, 0

    articles_found = []
    articles_in_range = 0
    articles_outside_range = 0
    consecutive_outside_range = 0
    max_consecutive_outside = 5

    # Save page source for debugging
    try:
        with open('debug_thamilan_page.html', 'w', encoding='utf-8') as f:
            f.write(driver.page_source)
        print(f"   [DEBUG] Saved page source to debug_thamilan_page.html")
    except:
        pass

    print(f"   Scraping articles...")

    processed_links = set()
    see_more_clicks = 0
    max_see_more_clicks = 15
    stop_scraping = False

    while not stop_scraping:
        # Get all currently loaded article cards
        cards = get_article_cards(driver)
        new_cards = [c for c in cards if c['link'] not in processed_links]
        
        if not new_cards:
            # No new articles found - try clicking "See More"
            if see_more_clicks < max_see_more_clicks:
                clicked = click_see_more(driver)
                if clicked:
                    see_more_clicks += 1
                    print(f"   [LOAD] Clicked See More ({see_more_clicks}), loading more articles...")
                    continue
                else:
                    print(f"   [LOAD] No more 'See More' button found, done")
                    break
            else:
                print(f"   [LOAD] Max See More clicks reached ({max_see_more_clicks})")
                break

        print(f"   [INFO] Found {len(new_cards)} new articles to process")

        for card in new_cards:
            if stop_scraping:
                break

            title = card['title']
            article_link = card['link']
            date_text = card['date_text']
            processed_links.add(article_link)

            article_date = parse_relative_date(date_text) if date_text else None

            article_num = len(processed_links)
            print(f"\n   Article #{article_num}: {title[:60]}...")
            print(f"     Link: {article_link}")
            print(f"     Date: '{date_text}' -> {article_date.strftime('%Y-%m-%d') if article_date else 'unknown'}")

            if article_date is None:
                print(f"     No date found, skipping")
                continue

            if not is_article_in_date_range(article_date, start_date, end_date):
                article_date_str = article_date.strftime('%Y-%m-%d')
                print(f"     [SKIP] Outside date range: {article_date_str}")
                articles_outside_range += 1
                
                # Only treat as consecutive outside range (for stopping) if it is older than our target range
                if article_date.date() < start_date:
                    consecutive_outside_range += 1
                else:
                    # Reset counter for newer articles so we can keep scanning down to older ones
                    consecutive_outside_range = 0
                
                days_before_range = (start_date - article_date.date()).days
                if days_before_range > 2:
                    print(f"     Article is {days_before_range} days before target range - stopping")
                    stop_scraping = True
                    break
                if consecutive_outside_range >= max_consecutive_outside:
                    print(f"\n   Stopping: {max_consecutive_outside} consecutive articles outside date range")
                    stop_scraping = True
                    break
                continue

            # Article is in range - extract full content
            articles_in_range += 1
            consecutive_outside_range = 0
            print(f"     In date range! Extracting full content...")

            original_window = driver.current_window_handle
            opened_new_tab = False
            final_date = article_date
            final_title = title
            final_image = ""
            final_description = ""
            extraction_successful = False

            try:
                windows_before = set(driver.window_handles)
                driver.execute_script("window.open(arguments[0], '_blank');", article_link)
                time.sleep(1)

                windows_after = set(driver.window_handles)
                new_windows = windows_after - windows_before

                if new_windows:
                    new_window = new_windows.pop()
                    driver.switch_to.window(new_window)
                    opened_new_tab = True
                    # Wait for page to be ready
                    try:
                        WebDriverWait(driver, 15).until(
                            lambda d: d.execute_script('return document.readyState') == 'complete'
                        )
                    except:
                        time.sleep(2)
                else:
                    driver.set_page_load_timeout(15)
                    driver.get(article_link)
                    opened_new_tab = False
                    time.sleep(1)

                try:
                    article_content = extract_article_content(driver)
                    if article_content:
                        final_date = article_content['date_published'] if article_content.get('date_published') else article_date
                        final_title = article_content['title'] if article_content.get('title') else title
                        final_image = article_content['image_url'] if article_content.get('image_url') else ""
                        extracted_desc = article_content.get('description', '').strip()
                        if extracted_desc and len(extracted_desc) > 0:
                            final_description = extracted_desc
                            extraction_successful = True
                            print(f"     Extracted full article ({len(final_description)} chars)")
                        else:
                            print(f"     Extracted description is empty")
                except Exception as extract_error:
                    print(f"     [EXTRACT] Error: {extract_error}")

                # Return to list page
                if opened_new_tab:
                    try:
                        driver.close()
                        driver.switch_to.window(original_window)
                    except:
                        driver.get(list_url)
                        time.sleep(1)
                else:
                    try:
                        driver.back()
                        time.sleep(1)
                    except:
                        driver.get(list_url)
                        time.sleep(1)

            except Exception as e:
                print(f"     Error during navigation/extraction: {e}")
                try:
                    if opened_new_tab:
                        driver.close()
                        driver.switch_to.window(original_window)
                    if driver.current_url != list_url:
                        driver.get(list_url)
                        time.sleep(2)
                except:
                    pass

            standardized_date = final_date.strftime("%Y-%m-%d %H:%M:%S")
            date_source = "Article page" if extraction_successful else f"List page: {date_text}"

            articles_found.append({
                'title': final_title,
                'link': article_link,
                'summary': final_description,
                'date': standardized_date,
                'image_url': final_image,
                'date_source': date_source
            })

            if extraction_successful:
                print(f"     Article saved with full content")
            else:
                print(f"     Article saved with list page data")

        # After processing all new cards, try to load more
        if not stop_scraping:
            if see_more_clicks < max_see_more_clicks:
                clicked = click_see_more(driver)
                if clicked:
                    see_more_clicks += 1
                    print(f"   [LOAD] Clicked See More ({see_more_clicks}), loading more articles...")
                else:
                    print(f"   [LOAD] No more 'See More' button found, done")
                    break
            else:
                print(f"   [LOAD] Max See More clicks reached ({max_see_more_clicks})")
                break

    print(f"\n   Page summary: {articles_in_range} in range, {articles_outside_range} outside range")
    return articles_found, articles_in_range, articles_outside_range

def main(start_date=None, end_date=None):
    """Main function."""
    
    if not start_date or not end_date:
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=1)
        print(f"[DATE] No date range provided, using default: {start_date} to {end_date}")
    else:
        print(f"[DATE] Scraping articles from {start_date} to {end_date}")
    
    print(f"[INFO] Starting Thamilan scraper (Local news)...")
    
    import os
    
    if USE_UNDETECTED:
        print("[INFO] Using undetected-chromedriver...")
        
        options = uc.ChromeOptions()
        options.page_load_strategy = 'eager'
        prefs = {
            "profile.default_content_setting_values": {
                "popups": 1
            }
        }
        options.add_experimental_option("prefs", prefs)
        
        try:
            # Let undetected-chromedriver auto-detect Chrome version
            driver = uc.Chrome(options=options, use_subprocess=True)
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
                    options_retry.add_experimental_option("prefs", {"profile.default_content_setting_values": {"popups": 1}})
                    driver = uc.Chrome(options=options_retry, use_subprocess=True, version_main=major_version)
                    print(f"[INFO] Undetected Chrome browser (forced version {major_version}) started successfully")
                except Exception as retry_err:
                    print(f"[WARNING] Retry with version_main={major_version} failed: {retry_err}")
                    print(f"[INFO] Trying with fresh ChromeOptions and version_main={major_version}...")
                    options_retry2 = uc.ChromeOptions()
                    options_retry2.page_load_strategy = 'eager'
                    options_retry2.add_experimental_option("prefs", {"profile.default_content_setting_values": {"popups": 1}})
                    driver = uc.Chrome(options=options_retry2, use_subprocess=True, version_main=major_version)
                    print(f"[INFO] Undetected Chrome browser (forced version {major_version}) started successfully")
            else:
                print(f"[INFO] Trying with fresh ChromeOptions...")
                options_retry2 = uc.ChromeOptions()
                options_retry2.page_load_strategy = 'eager'
                options_retry2.add_experimental_option("prefs", {"profile.default_content_setting_values": {"popups": 1}})
                driver = uc.Chrome(options=options_retry2, use_subprocess=True)
                print(f"[INFO] Undetected Chrome browser started successfully")
    else:
        print("[WARNING] undetected-chromedriver not installed")
        print("[INFO] Falling back to regular Selenium...")
        
        chrome_options = Options()
        chrome_options.page_load_strategy = 'eager'
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
        
        prefs = {
            "profile.default_content_setting_values": {
                "popups": 1
            }
        }
        chrome_options.add_experimental_option("prefs", prefs)

        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        print(f"[INFO] Chrome browser started successfully")
        
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    
    driver.set_page_load_timeout(60)
    print(f"[INFO] Page load timeout set to 60 seconds")

    all_articles = []
    total_articles_in_range = 0
    total_articles_outside_range = 0
    scraped_urls = set()
    
    for category_name, category_url in CATEGORY_URLS:
        print(f"\n[INFO] === Processing {category_name} ===")
        
        try:
            articles, page_in_range, page_outside_range = process_articles_from_page(
                driver, category_url, start_date, end_date
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
        except Exception as e:
            print(f"  [ERROR] Error processing {category_name}: {e}")
            import traceback
            traceback.print_exc()
    
    print(f"\n[INFO] Closing browser...")
    try:
        driver.quit()
        print(f"[INFO] Browser closed successfully")
    except Exception as e:
        print(f"[WARNING] Error closing browser: {e}")
        try:
            driver.close()
        except:
            pass
    
    print(f"\n[INFO] Final Results:")
    print(f"  [INFO] Articles in date range: {total_articles_in_range}")
    print(f"  [INFO] Articles outside range: {total_articles_outside_range}")
    print(f"  [INFO] Total articles to save: {len(all_articles)}")

    import os
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    data_dir = os.path.join(project_root, 'data')
    os.makedirs(data_dir, exist_ok=True)

    json_filename = os.path.join(data_dir, 'thamilan_latest_news.json')

    if not all_articles:
        print(f" [INFO] 0 articles scraped. Preserving existing data in {json_filename} intact.")
    else:
        with open(json_filename, 'w', encoding='utf-8') as jsonfile:
            json.dump(all_articles, jsonfile, ensure_ascii=False, indent=2)

    if not all_articles:
        print(f"[WARNING] No articles found in the specified date range ({start_date} to {end_date})")
        print(f"[INFO] Saved empty JSON array to {json_filename}")
    else:
        print(f"\n[INFO] Scraping complete!")
        print(f"[INFO] Saved {len(all_articles)} articles to {json_filename}")
        print(f"[INFO] Date range: {start_date} to {end_date}")

        articles_with_images = sum(1 for article in all_articles if article['image_url'] and article['image_url'] != '')
        print(f"[IMAGE] Articles with images: {articles_with_images}/{len(all_articles)}")

if __name__ == "__main__":
    import os

    _scraper_dir = os.path.dirname(os.path.abspath(__file__))
    if _scraper_dir not in sys.path:
        sys.path.insert(0, _scraper_dir)
    from incremental import is_incremental_mode

    if is_incremental_mode():
        from incremental_outlets import run_incremental_for_module

        run_incremental_for_module("thamilan_selenium_json")
        sys.exit(0)

    if len(sys.argv) >= 3:
        try:
            start_date = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
            end_date = datetime.strptime(sys.argv[2], "%Y-%m-%d").date()
            
            main(start_date, end_date)
        except ValueError as e:
            print(f"[ERROR] Invalid date format. Use YYYY-MM-DD. Error: {e}")
            print("[INFO] Example: python thamilan_selenium_json.py 2026-02-09 2026-02-09")
    else:
        main()