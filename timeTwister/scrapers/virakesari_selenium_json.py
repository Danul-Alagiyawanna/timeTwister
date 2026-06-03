import sys
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

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

# Always import selenium components (needed for fallback or if undetected-chromedriver not available)
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager

from bs4 import BeautifulSoup
import time
import json
import sys
from datetime import datetime, timedelta
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import re
import threading
from functools import wraps

BASE_URL = "https://www.virakesari.lk/category/local"

class TimeoutError(Exception):
    """Custom timeout exception for extraction"""
    pass

def extract_with_timeout(driver, timeout_seconds=30):
    """Extract article content with timeout. Returns None if timeout exceeded."""
    start_time = time.time()
    
    try:
        # Pass timeout to extraction function so it can check elapsed time
        result = extract_article_content(driver, max_elapsed_time=timeout_seconds)
        elapsed = time.time() - start_time
        
        if elapsed > timeout_seconds:
            print(f"     [TIMEOUT] Extraction took {elapsed:.1f}s (exceeded {timeout_seconds}s limit)")
            return None
        
        return result
    except TimeoutError:
        elapsed = time.time() - start_time
        print(f"     [TIMEOUT] Extraction timeout exceeded after {elapsed:.1f}s")
        return None
    except Exception as e:
        elapsed = time.time() - start_time
        if elapsed > timeout_seconds:
            print(f"     [TIMEOUT] Extraction failed after {elapsed:.1f}s (exceeded {timeout_seconds}s limit): {e}")
            return None
        # Re-raise if it's a real error and didn't timeout
        raise

def extract_article_content(driver, max_elapsed_time=30):
    """Extract detailed content from article page."""
    start_time = time.time()
    print(f"     [EXTRACT] Function started")
    try:
        print(f"     [EXTRACT] Current URL: {driver.current_url}")
        print(f"     [EXTRACT] Page title: {driver.title}")
        
        # Check elapsed time
        if time.time() - start_time > max_elapsed_time:
            raise TimeoutError("Extraction timeout exceeded")
        
        # Wait for page to load
        print(f"     [EXTRACT] Waiting 2 seconds for initial page load...")
        time.sleep(2)

        # Check elapsed time
        if time.time() - start_time > max_elapsed_time:
            raise TimeoutError("Extraction timeout exceeded")

        soup = BeautifulSoup(driver.page_source, 'html.parser')

        # Extract publication date from the page
        date_published = None
        # Try to find date in various formats on the page
        try:
            # Look for date in the article meta or content
            date_elements = soup.find_all(['time', 'span', 'div'], class_=re.compile(r'date|time|publish', re.I))
            for elem in date_elements:
                if elem.get('datetime'):
                    try:
                        date_published = datetime.fromisoformat(elem['datetime'].replace('Z', '+00:00'))
                        print(f"     Found date from datetime attribute: {date_published}")
                        break
                    except:
                        pass
        except Exception as e:
            print(f"     Error finding date: {e}")

        # Extract image URL from OpenGraph meta tag
        image_url = ""
        og_image = soup.find('meta', attrs={'property': 'og:image'})
        if og_image and og_image.has_attr('content'):
            image_url = og_image['content']
            print(f"     Found image: {image_url[:60]}...")

        # Extract full article text from paragraph tags
        full_article_text = ""
        extraction_successful = False
        
        # Strategy 1: Try to find article content div
        if not extraction_successful:
            try:
                # Look for common article content containers
                article_selectors = [
                    'div.article-content',
                    'div.post-content',
                    'div.entry-content',
                    'article',
                    'div[class*="content"]'
                ]
                
                for selector in article_selectors:
                    article_div = soup.select_one(selector)
                    if article_div:
                        paragraphs = article_div.find_all('p')
                        paragraph_texts = [p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)]
                        if paragraph_texts:
                            full_article_text = "\n\n".join(paragraph_texts)
                            extraction_successful = True
                            print(f"     ✓ Extracted {len(paragraph_texts)} paragraphs using selector '{selector}' ({len(full_article_text)} chars)")
                            break
            except Exception as e:
                print(f"     Content div extraction failed: {e}")
        
        # Strategy 2: Fallback - get all paragraphs from the page
        if not extraction_successful:
            try:
                all_paragraphs = soup.find_all('p')
                paragraph_texts = []
                for p in all_paragraphs:
                    text = p.get_text(strip=True)
                    # Filter out navigation, footer, and short paragraphs
                    if text and len(text) > 50:
                        paragraph_texts.append(text)
                
                if paragraph_texts:
                    full_article_text = "\n\n".join(paragraph_texts)
                    extraction_successful = True
                    print(f"     ✓ Extracted {len(paragraph_texts)} paragraphs using fallback method ({len(full_article_text)} chars)")
            except Exception as e:
                print(f"     Fallback extraction failed: {e}")
        
        if not extraction_successful:
            print(f"     WARNING: Could not extract full article text from any method")

        # Extract title from the page
        title = ""
        og_title = soup.find('meta', attrs={'property': 'og:title'})
        if og_title and og_title.has_attr('content'):
            title = og_title['content']
        else:
            # Fallback to page title
            title_tag = soup.find('title')
            if title_tag:
                title = title_tag.get_text(strip=True)

        # Get current URL
        current_url = driver.current_url

        print(f"     [EXTRACT] Extraction complete. Summary length: {len(full_article_text)} chars")
        print(f"     [EXTRACT] Returning results...")

        return {
            'date_published': date_published,
            'image_url': image_url,
            'description': full_article_text,
            'title': title,
            'link': current_url
        }

    except Exception as e:
        print(f"     [EXTRACT] ✗ Error extracting metadata: {e}")
        import traceback
        print(f"     [EXTRACT] Full traceback:")
        traceback.print_exc()
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
        return False  # Exclude articles without parseable dates for accuracy
    
    # Convert to date only for comparison
    article_date = article_date.date()
    return start_date <= article_date <= end_date

def parse_tamil_date(date_text):
    """Parse Tamil date format from Virakesari (e.g., '09 Feb, 2026 | 01:03 PM')"""
    try:
        # Clean up the date text
        date_text = date_text.strip()
        
        # Format: "09 Feb, 2026 | 01:03 PM"
        if '|' in date_text:
            date_part, time_part = date_text.split('|')
            date_part = date_part.strip()
            time_part = time_part.strip()
            
            # Parse date and time
            datetime_str = f"{date_part} {time_part}"
            article_date = datetime.strptime(datetime_str, "%d %b, %Y %I:%M %p")
            return article_date
    except Exception as e:
        print(f"     Error parsing Tamil date '{date_text}': {e}")
    
    return None

def process_articles_from_list_page(driver, list_url, start_date, end_date):
    """Process articles from a single list page using XPath navigation."""
    print(f"\n[INFO] Processing articles from: {list_url}")
    print(f"[DEBUG] Navigating to: {list_url}")

    # Navigate to list page
    try:
        print(f"[DEBUG] Calling driver.get()...")
        driver.get(list_url)
        print(f"[DEBUG] driver.get() completed")
        print(f"[DEBUG] Waiting for page to be ready...")
        
        # Wait for page to be in ready state
        WebDriverWait(driver, 30).until(
            lambda d: d.execute_script('return document.readyState') == 'complete'
        )
        print(f"[DEBUG] Page ready state: complete")
        
        time.sleep(5)  # Extra wait for dynamic content
        print(f"[DEBUG] Current page title: {driver.title}")
        print(f"[DEBUG] Current URL: {driver.current_url}")
        print(f"[DEBUG] Page source length: {len(driver.page_source)} characters")
    except Exception as e:
        print(f"[ERROR] Failed to navigate to page: {e}")
        import traceback
        traceback.print_exc()
        return [], 0, 0

    try:
        print(f"[DEBUG] Waiting for news-item elements...")
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CLASS_NAME, "news-item"))
        )
        print(f"   List page loaded successfully")
    except Exception as e:
        print(f"   Timeout waiting for articles: {e}")
        print(f"[DEBUG] Page source length: {len(driver.page_source)}")
        return [], 0, 0

    articles_found = []
    articles_in_range = 0
    articles_outside_range = 0
    consecutive_outside_range = 0
    max_consecutive_outside = 2
    page_scraped_links = set()

    # Parse list page with BeautifulSoup
    soup = BeautifulSoup(driver.page_source, 'html.parser')
    cards = soup.find_all('a', class_='news-item')
    print(f"   Found {len(cards)} elements with class 'news-item'")

    for i, card in enumerate(cards, 1):
        try:
            # Extract link
            article_link = card.get('href', '').strip()
            if not article_link:
                continue
            if not article_link.startswith('http'):
                article_link = "https://www.virakesari.lk" + article_link

            # Deduplicate links on the same page
            if article_link in page_scraped_links:
                continue
            page_scraped_links.add(article_link)

            # Extract title (prioritize title attribute to avoid truncation)
            title_el = card.find(['h3', 'h4'])
            title = ""
            if title_el:
                title = title_el.get('title', '').strip()
                if not title:
                    title = title_el.get_text(strip=True)

            # Extract date text
            date_el = card.find(class_=re.compile(r'date|time', re.I))
            date_text = date_el.get_text(strip=True) if date_el else ""

            print(f"\n   Article {i}: {title[:60]}...")
            print(f"     Link: {article_link}")
            print(f"     Date text: {date_text}")

            # Parse the date from the date text
            article_date = None
            if date_text:
                article_date = parse_tamil_date(date_text)
                if article_date:
                    print(f"     Parsed date: {article_date.strftime('%Y-%m-%d %H:%M:%S')}")

            # Check if article is in date range
            if article_date is None:
                print(f"     No date found, skipping article")
                consecutive_outside_range += 1
            elif is_article_in_date_range(article_date, start_date, end_date):
                print(f"     Article is in date range! Attempting to extract full content...")

                # Store the current window handle (list page)
                original_window = driver.current_window_handle
                opened_new_tab = False
                
                # Initialize with list page data as fallback
                final_date = article_date
                final_title = title
                final_image = ""
                final_description = ""  # No description on list page
                extraction_successful = False
                
                try:
                    # Try to open article in a new tab
                    print(f"     [NEW TAB] Attempting to open article in new tab: {article_link}")
                    
                    # Store current window handles
                    windows_before = set(driver.window_handles)
                    
                    # Open article link in a new tab using JavaScript
                    driver.execute_script("window.open(arguments[0], '_blank');", article_link)
                    time.sleep(2)  # Wait for new tab to open
                    
                    # Get new window handles
                    windows_after = set(driver.window_handles)
                    new_windows = windows_after - windows_before
                    
                    if new_windows:
                        # New tab opened successfully
                        new_window = new_windows.pop()
                        driver.switch_to.window(new_window)
                        opened_new_tab = True
                        print(f"     [NEW TAB] ✓ Switched to new tab")
                        
                        # Wait for page to load
                        print(f"     [NEW TAB] Waiting for page to load...")
                        time.sleep(3)
                        
                        # Verify we're on the article page
                        current_url = driver.current_url
                        if current_url != article_link and article_link not in current_url:
                            print(f"     [WARNING] URL mismatch in new tab - expected: {article_link}, got: {current_url}")
                            print(f"     [FALLBACK] Will use list page data")
                            # Close the tab and switch back
                            driver.close()
                            driver.switch_to.window(original_window)
                            raise Exception("URL mismatch in new tab")
                        
                        print(f"     [NEW TAB] Current URL: {current_url}")
                        print(f"     [NEW TAB] Page title: {driver.title[:80]}...")
                    else:
                        # New tab didn't open (popup blocker or other issue)
                        print(f"     [WARNING] New tab did not open (popup blocker?), trying direct navigation...")
                        # Fallback to direct navigation
                        try:
                            driver.set_page_load_timeout(30)
                            driver.get(article_link)
                            opened_new_tab = False
                            print(f"     [NAVIGATE] Direct navigation complete, waiting for page to load...")
                            time.sleep(3)
                            
                            # Verify navigation was successful
                            current_url = driver.current_url
                            if current_url != article_link and article_link not in current_url:
                                print(f"     [WARNING] URL mismatch - expected: {article_link}, got: {current_url}")
                                print(f"     [FALLBACK] Will use list page data")
                                raise Exception("URL mismatch after navigation")
                            
                            print(f"     [NAVIGATE] Current URL: {current_url}")
                            print(f"     [NAVIGATE] Page title: {driver.title[:80]}...")
                        except Exception as nav_error:
                            print(f"     [NAVIGATE] ✗ Navigation timeout/error: {nav_error}")
                            print(f"     [FALLBACK] Will use list page data")
                            raise

                    # Extract detailed content from the article page with 30 second timeout
                    print(f"     [EXTRACT] Starting content extraction (30s timeout)...")
                    try:
                        article_content = extract_with_timeout(driver, timeout_seconds=30)
                        
                        if article_content is None:
                            # Timeout occurred
                            print(f"     [TIMEOUT] Extraction exceeded 30 seconds, using list page data")
                        else:
                            print(f"     [EXTRACT] Content extraction completed")
                            print(f"     [EXTRACT] Got article_content keys: {list(article_content.keys())}")
                            print(f"     [EXTRACT] Description length: {len(article_content.get('description', ''))}")

                            # Use more accurate date from article page if available
                            final_date = article_content['date_published'] if article_content.get('date_published') else article_date
                            final_title = article_content['title'] if article_content.get('title') else title
                            final_image = article_content['image_url'] if article_content.get('image_url') else ""
                                    
                            # Prioritize extracted full article text
                            extracted_desc = article_content.get('description', '').strip()
                            if extracted_desc and len(extracted_desc) > 0:
                                final_description = extracted_desc
                                extraction_successful = True
                                print(f"     ✓ Using extracted full article text ({len(final_description)} chars)")
                            else:
                                print(f"     ⚠ Extracted description is empty")
                    except Exception as extract_error:
                        print(f"     [EXTRACT] ✗ Error during extraction: {extract_error}")
                        print(f"     [FALLBACK] Will use list page data")
                    
                    # Return to list page (close tab if we opened one, or navigate back if we went directly)
                    if opened_new_tab:
                        # We opened a new tab, close it and switch back
                        print(f"     [CLOSE TAB] Closing article tab and returning to list page...")
                        try:
                            driver.close()
                            driver.switch_to.window(original_window)
                            time.sleep(1)  # Brief wait after switching back
                        except Exception as close_error:
                            print(f"     [WARNING] Error closing tab: {close_error}, reloading list page...")
                            driver.get(list_url)
                            time.sleep(2)
                    else:
                        # We navigated directly, go back with timeout protection
                        print(f"     [BACK] Navigating back to list page...")
                        try:
                            # Set a shorter timeout for back navigation
                            driver.set_page_load_timeout(10)
                            driver.back()
                            time.sleep(2)  # Wait for list page to reload
                        except Exception as back_error:
                            print(f"     [WARNING] Back() failed ({back_error}), reloading list page...")
                            try:
                                driver.set_page_load_timeout(30)
                                driver.get(list_url)
                                time.sleep(3)
                            except Exception as reload_error:
                                print(f"     [ERROR] Could not reload list page: {reload_error}")

                except Exception as e:
                    print(f"     ✗ Error during navigation/extraction: {e}")
                    print(f"     [FALLBACK] Using list page data")
                    # Ensure we're back on the list page
                    try:
                        # If we opened a new tab, close it first
                        if opened_new_tab:
                            try:
                                all_windows = driver.window_handles
                                current_window = driver.current_window_handle
                                if len(all_windows) > 1:
                                    # Close current tab (article tab)
                                    driver.close()
                                    # Switch back to original window
                                    if original_window in all_windows:
                                        driver.switch_to.window(original_window)
                                    else:
                                        # Original window might be closed, switch to any remaining window
                                        remaining_windows = [w for w in all_windows if w != current_window]
                                        if remaining_windows:
                                            driver.switch_to.window(remaining_windows[0])
                                print(f"     [RECOVER] Closed article tab and returned to list page")
                            except Exception as tab_error:
                                print(f"     [RECOVER] Error closing tab: {tab_error}")
                                # Try to switch to original window anyway
                                try:
                                    driver.switch_to.window(original_window)
                                except:
                                    pass
                        
                        # Verify we're on the list page
                        if driver.current_url != list_url:
                            print(f"     [RECOVER] Returning to list page...")
                            try:
                                driver.back()
                                time.sleep(2)
                            except:
                                pass
                            
                            if driver.current_url != list_url:
                                driver.get(list_url)
                                time.sleep(3)
                    except Exception as recover_error:
                        print(f"     [RECOVER] Error recovering: {recover_error}")
                        try:
                            # Last resort: reload list page
                            driver.get(list_url)
                            time.sleep(3)
                        except:
                            pass
                
                # Always save the article (with either extracted or fallback data)
                # Standardized date format
                standardized_date = final_date.strftime("%Y-%m-%d %H:%M:%S")
                
                date_source = "Article page" if extraction_successful and final_date != article_date else f"List page: {date_text}"

                articles_found.append({
                    'title': final_title,
                    'link': article_link,
                    'summary': final_description,
                    'date': standardized_date,
                    'image_url': final_image,
                    'date_source': date_source
                })
                articles_in_range += 1
                consecutive_outside_range = 0  # Reset counter

                if extraction_successful:
                    print(f"     ✓ Article saved with full extracted content")
                else:
                    print(f"     ✓ Article saved with list page data (fallback)")
            else:
                article_date_str = article_date.strftime('%Y-%m-%d')
                print(f"     [SKIP] Article outside date range: {article_date_str}")
                articles_outside_range += 1
                consecutive_outside_range += 1

                # Check if article is significantly older than our target range
                days_before_range = (start_date - article_date.date()).days
                if days_before_range > 2:
                    print(f"     Article is {days_before_range} days before target range - stopping immediately")
                    break

            # Check if we should stop (too many consecutive articles outside range)
            if consecutive_outside_range >= max_consecutive_outside:
                print(f"\n   Stopping: {max_consecutive_outside} consecutive articles outside date range")
                break

        except Exception as e:
            print(f"     Error with article {i}: {e}")
            continue
    
    print(f"\n   Page summary: {articles_in_range} in range, {articles_outside_range} outside range")
    return articles_found, articles_in_range, articles_outside_range

def main(start_date=None, end_date=None):
    """Main function with click-and-back navigation approach."""
    
    # Default to yesterday and today if no dates provided
    if not start_date or not end_date:
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=1)
        print(f"[DATE] No date range provided, using default: {start_date} to {end_date}")
    else:
        print(f"[DATE] Scraping articles from {start_date} to {end_date}")
    
    print("[INFO] Starting Virakesari scraper with click-and-back navigation...")
    
    import os
    
    if USE_UNDETECTED:
        print("[INFO] Using undetected-chromedriver to bypass Cloudflare...")
        
        options = uc.ChromeOptions()
        options.page_load_strategy = 'eager'
        # options.add_argument('--headless')  # Disabled for debugging
        
        # Disable popup blocking to allow new tabs
        prefs = {
            "profile.default_content_setting_values": {
                "popups": 1  # Allow popups
            }
        }
        options.add_experimental_option("prefs", prefs)
        
        print(f"[INFO] Starting undetected Chrome (bypasses Cloudflare automatically)...")
        
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
        print("[WARNING] undetected-chromedriver not installed. Install with: pip install undetected-chromedriver")
        print("[INFO] Falling back to regular Selenium (may be blocked by Cloudflare)...")
        
        chrome_options = Options()
        chrome_options.page_load_strategy = 'eager'
        # chrome_options.add_argument('--headless')  # Disabled for debugging
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
        
        # Disable popup blocking to allow new tabs
        prefs = {
            "profile.default_content_setting_values": {
                "popups": 1  # Allow popups
            }
        }
        chrome_options.add_experimental_option("prefs", prefs)

        service = Service(ChromeDriverManager().install())
        print(f"[INFO] Starting Chrome browser...")
        driver = webdriver.Chrome(service=service, options=chrome_options)
        print(f"[INFO] Chrome browser started successfully")
        
        # Add stealth settings
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        print(f"[INFO] Stealth settings applied")
    
    # Set page load timeout to prevent hanging
    driver.set_page_load_timeout(60)  # 60 seconds timeout
    print(f"[INFO] Page load timeout set to 60 seconds")

    categories = ["local", "world", "sports", "feature", "cinema", "business"]
    scraped_urls = set()
    all_articles = []
    total_articles_in_range = 0
    total_articles_outside_range = 0
    max_pages = 10  # Process more pages since we can stop early
    
    print(f"\n[INFO] Processing articles page by page with click-and-back navigation...")
    
    for category in categories:
        cat_url = f"https://www.virakesari.lk/category/{category}"
        print(f"\n{'='*60}")
        print(f"[INFO] Scraping category: {category} ({cat_url})")
        print(f"{'='*60}")
        consecutive_empty_pages = 0
        max_consecutive_empty = 3
        
        for page_num in range(1, max_pages + 1):
            if page_num == 1:
                list_url = cat_url
            else:
                list_url = f"{cat_url}?page={page_num}"
            
            print(f"\n[INFO] Page {page_num}:")
            
            try:
                articles, page_in_range, page_outside_range = process_articles_from_list_page(
                    driver, list_url, start_date, end_date
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
                
                # Check if we should continue to next page
                if page_in_range == 0:
                    consecutive_empty_pages += 1
                    print(f"  [WARNING] No articles in range on this page ({consecutive_empty_pages}/{max_consecutive_empty} consecutive)")
                    if consecutive_empty_pages >= max_consecutive_empty:
                        print(f"  [ERROR] Stopping category {category}: {max_consecutive_empty} consecutive pages with no articles in date range")
                        break
                else:
                    consecutive_empty_pages = 0  # Reset counter
                
                # Brief delay between pages
                time.sleep(2)
                
            except Exception as e:
                print(f"  [ERROR] Error processing category {category} page {page_num}: {e}")
                if page_num == 1:
                    print(f"  [WARNING] Category {category} page 1 failed, skipping category...")
                    break
                continue
    
    # Properly close the driver
    print(f"\n[INFO] Closing browser...")
    try:
        # Quit the driver (this properly closes the browser)
        driver.quit()
        print(f"[INFO] Browser closed successfully")
    except Exception as e:
        print(f"[WARNING] Error closing browser (may be harmless): {e}")
        try:
            # Try alternative cleanup
            driver.close()
        except:
            pass
    
    print(f"\n[INFO] Final Results:")
    print(f"  [INFO] Articles in date range: {total_articles_in_range}")
    print(f"  [INFO] Articles outside range: {total_articles_outside_range}")
    print(f"  [INFO] Total articles to save: {len(all_articles)}")

    # Save to data directory (even if empty)
    import os
    os.makedirs('data', exist_ok=True)
    import os
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    data_dir = os.path.join(project_root, 'data')
    os.makedirs(data_dir, exist_ok=True)
    json_filename = os.path.join(data_dir, 'virakesari_latest_news.json')

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
        print(f"\n[INFO] Enhanced scraping complete!")
        print(f"[INFO] Saved {len(all_articles)} articles to {json_filename}")
        print(f"[INFO] Date range: {start_date} to {end_date}")

        # Count articles with images
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

        run_incremental_for_module("virakesari_selenium_json")
        sys.exit(0)

    # Check if date range is provided as command line arguments
    if len(sys.argv) >= 3:
        try:
            start_date = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
            end_date = datetime.strptime(sys.argv[2], "%Y-%m-%d").date()
            main(start_date, end_date)
        except ValueError as e:
            print(f"[ERROR] Invalid date format. Use YYYY-MM-DD. Error: {e}")
            print("[INFO] Example: python virakesari_selenium_json.py 2026-02-09 2026-02-09")
    else:
        main()  # Use default date range