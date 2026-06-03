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
from datetime import datetime, timedelta
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import re

# Reconfigure stdout/stderr to use UTF-8 to prevent UnicodeEncodeError on Windows
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except AttributeError:
    pass

BASE_URL = "https://www.dinamina.lk/category/local/"

class TimeoutError(Exception):
    """Custom timeout exception for extraction"""
    pass

def extract_with_timeout(driver, timeout_seconds=30):
    """Extract article content with timeout. Returns None if timeout exceeded."""
    start_time = time.time()
    
    try:
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
        raise

def extract_article_content(driver, max_elapsed_time=30):
    """Extract detailed content from article page."""
    start_time = time.time()
    print(f"     [EXTRACT] Function started")
    try:
        print(f"     [EXTRACT] Current URL: {driver.current_url}")
        print(f"     [EXTRACT] Page title: {driver.title}")
        
        if time.time() - start_time > max_elapsed_time:
            raise TimeoutError("Extraction timeout exceeded")
        
        print(f"     [EXTRACT] Waiting 2 seconds for initial page load...")
        time.sleep(2)

        if time.time() - start_time > max_elapsed_time:
            raise TimeoutError("Extraction timeout exceeded")

        soup = BeautifulSoup(driver.page_source, 'html.parser')

        # Extract publication date
        date_published = None
        date_meta = soup.find('meta', attrs={'property': 'article:published_time'})
        if date_meta and date_meta.has_attr('content'):
            try:
                date_str = date_meta['content']
                date_str_clean = date_str.split('+')[0].split('T')
                if len(date_str_clean) == 2:
                    date_published = datetime.strptime(f"{date_str_clean[0]} {date_str_clean[1]}", "%Y-%m-%d %H:%M:%S")
                    print(f"     Found exact date: {date_published}")
            except ValueError as e:
                print(f"     Error parsing date '{date_meta['content']}': {e}")
        
        if not date_published:
            # Try alternative date format
            time_tag = soup.find('time')
            if time_tag and time_tag.has_attr('datetime'):
                try:
                    date_str = time_tag['datetime']
                    date_str_clean = date_str.split('+')[0].split('T')
                    if len(date_str_clean) == 2:
                        date_published = datetime.strptime(f"{date_str_clean[0]} {date_str_clean[1]}", "%Y-%m-%d %H:%M:%S")
                        print(f"     Found date from time tag: {date_published}")
                except:
                    pass

        # Extract image URL
        image_url = ""
        og_image = soup.find('meta', attrs={'property': 'og:image'})
        if og_image and og_image.has_attr('content'):
            image_url = og_image['content']
            print(f"     Found image: {image_url[:60]}...")

        # Extract full article text
        full_article_text = ""
        extraction_successful = False
        
        if time.time() - start_time > max_elapsed_time:
            raise TimeoutError("Extraction timeout exceeded")
        
        # Strategy 1: Try to find article content
        if not extraction_successful:
            try:
                if time.time() - start_time > max_elapsed_time:
                    raise TimeoutError("Extraction timeout exceeded")
                
                print(f"     [EXTRACT] Waiting for article content...")
                article_body = None
                
                # Try multiple selectors for Dinamina
                selectors = [
                    "article .entry-content",
                    ".entry-content",
                    "article .post-content",
                    ".post-content",
                    ".article-content",
                    "article"
                ]
                
                for selector in selectors:
                    try:
                        article_body = WebDriverWait(driver, 5).until(
                            EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                        )
                        print(f"     [EXTRACT] ✓ Found article content using selector: {selector}")
                        break
                    except:
                        continue
                
                if article_body:
                    if time.time() - start_time > max_elapsed_time:
                        raise TimeoutError("Extraction timeout exceeded")
                    
                    time.sleep(1)
                    
                    # Find all paragraph tags
                    paragraphs = article_body.find_elements(By.TAG_NAME, "p")
                    print(f"     [DEBUG] Found {len(paragraphs)} <p> tags in article body")
                    
                    paragraph_texts = []
                    
                    if len(paragraphs) == 0:
                        print(f"     [DEBUG] No <p> tags, trying to get text directly from div...")
                        try:
                            all_text = article_body.text.strip()
                            if all_text and len(all_text) > 50:
                                text_chunks = [chunk.strip() for chunk in all_text.split('\n') if chunk.strip()]
                                paragraph_texts = [chunk for chunk in text_chunks if len(chunk) > 20]
                                print(f"     [DEBUG] Got {len(paragraph_texts)} text chunks from div")
                        except Exception as e:
                            print(f"     [DEBUG]   Error getting text from div: {e}")
                    else:
                        for idx, p in enumerate(paragraphs):
                            try:
                                text = p.text.strip()
                                if text:
                                    paragraph_texts.append(text)
                                    if idx < 3:
                                        print(f"     [DEBUG]   Paragraph {idx+1}: {text[:100]}...")
                            except Exception as e:
                                print(f"     [DEBUG]   Error getting text from paragraph {idx+1}: {e}")
                                continue
                    
                    if paragraph_texts:
                        full_article_text = "\n\n".join(paragraph_texts)
                        extraction_successful = True
                        print(f"     ✓ Extracted {len(paragraph_texts)} paragraphs ({len(full_article_text)} chars)")
                else:
                    print(f"     [DEBUG] Could not find article body with any selector")
            except Exception as e:
                print(f"     Strategy 1 extraction failed: {e}")
        
        # Strategy 2: BeautifulSoup fallback
        if not extraction_successful:
            try:
                # Try finding entry-content or post-content
                article_div = soup.find('div', class_='entry-content')
                if not article_div:
                    article_div = soup.find('div', class_='post-content')
                if not article_div:
                    article_div = soup.find('div', class_='article-content')
                if not article_div:
                    article_div = soup.find('article')
                
                if article_div:
                    paragraphs = article_div.find_all('p')
                    paragraph_texts = [p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)]
                    
                    if not paragraph_texts:
                        print(f"     [DEBUG] No <p> tags in BeautifulSoup, getting text directly...")
                        all_text = article_div.get_text(separator='\n', strip=True)
                        text_chunks = [chunk.strip() for chunk in all_text.split('\n') if chunk.strip()]
                        paragraph_texts = [chunk for chunk in text_chunks if len(chunk) > 20]
                        print(f"     [DEBUG] Got {len(paragraph_texts)} text chunks via BeautifulSoup")
                    
                    if paragraph_texts:
                        full_article_text = "\n\n".join(paragraph_texts)
                        extraction_successful = True
                        print(f"     Extracted {len(paragraph_texts)} paragraphs using BeautifulSoup")
            except Exception as e:
                print(f"     BeautifulSoup fallback failed: {e}")
        
        if not extraction_successful:
            print(f"     WARNING: Could not extract full article text from any method")

        # Extract title
        title = ""
        og_title = soup.find('meta', attrs={'property': 'og:title'})
        if og_title and og_title.has_attr('content'):
            title = og_title['content']

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
        return False
    
    article_date = article_date.date()
    return start_date <= article_date <= end_date

def parse_dinamina_date(date_text):
    """Parse Dinamina date format."""
    try:
        # Format: "January 30, 2026" or "2026-01-30"
        article_date = datetime.strptime(date_text.strip(), "%B %d, %Y")
        return article_date
    except:
        pass
    
    # Try other formats
    for fmt in ["%d %B %Y", "%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y"]:
        try:
            article_date = datetime.strptime(date_text.strip(), fmt)
            return article_date
        except:
            continue
    
    return None

def process_articles_from_page(driver, list_url, start_date, end_date):
    """Process articles from single page with progressive scrolling."""
    print(f"\n[INFO] Processing articles from: {list_url}")
    print(f"[DEBUG] Navigating to: {list_url}")

    try:
        print(f"[DEBUG] Calling driver.get()...")
        driver.get(list_url)
        print(f"[DEBUG] driver.get() completed")
        print(f"[DEBUG] Waiting for page to be ready...")
        
        WebDriverWait(driver, 30).until(
            lambda d: d.execute_script('return document.readyState') == 'complete'
        )
        print(f"[DEBUG] Page ready state: complete")
        
        time.sleep(3)
        
        print(f"[DEBUG] Current page title: {driver.title}")
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
    max_consecutive_outside = 3
    
    print(f"   Scanning for articles with progressive scrolling...")

    # Progressive scrolling approach - scan and process articles one by one
    max_articles = 100
    scroll_increment = 600
    
    # XPath patterns for Dinamina local news
    # First article has special structure: section/article
    # Rest are li[1]/article, li[2]/article, etc.
    
    article_idx = 0
    consecutive_not_found = 0
    max_consecutive_not_found = 3
    
    # Scroll to top first
    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(0.5)
    
    while article_idx < max_articles and consecutive_not_found < max_consecutive_not_found:
        heading_element = None
        date_element = None
        
        # Determine XPath based on article index
        if article_idx == 0:
            # First article - special pattern (section/article)
            heading_xpath = "/html/body/div[1]/div[3]/div[1]/div/ul/section/article/div[2]/div[1]/h2/a"
            date_xpath = "/html/body/div[1]/div[3]/div[1]/div/ul/section/article/div[2]/div[1]/div[2]/span[1]/time"
        elif article_idx >= 1 and article_idx <= 4:
            # Articles 1-4 (li[1] to li[4]) - pattern without extra div
            li_idx = article_idx  # li[1], li[2], li[3], li[4]
            heading_xpath = f"/html/body/div[1]/div[3]/div[1]/div/ul/li[{li_idx}]/article/div[2]/h2/a"
            date_xpath = f"/html/body/div[1]/div[3]/div[1]/div/ul/li[{li_idx}]/article/div[2]/div[2]/span/time"
        else:
            # Articles 5+ (li[5], li[6], li[7]...) - pattern WITH extra div layer
            li_idx = article_idx  # li[5], li[6], li[7], etc.
            heading_xpath = f"/html/body/div[1]/div[3]/div[1]/div/ul/li[{li_idx}]/article/div[2]/div/h2/a"
            date_xpath = f"/html/body/div[1]/div[3]/div[1]/div/ul/li[{li_idx}]/article/div[2]/div/div[2]/span/time"
        
        # Try to find the article with progressive scrolling
        found_this_article = False
        for scroll_attempt in range(8):
            try:
                heading_element = driver.find_element(By.XPATH, heading_xpath)
                date_element = driver.find_element(By.XPATH, date_xpath)
                print(f"   [SCAN] Found article {article_idx + 1} (using pattern: {'section' if article_idx == 0 else ('li[1-4]' if article_idx <= 4 else 'li[5+]')})")
                consecutive_not_found = 0
                found_this_article = True
                break
            except:
                if scroll_attempt < 7:
                    # Scroll down to look for more content
                    driver.execute_script(f"window.scrollBy(0, {scroll_increment});")
                    time.sleep(0.8)
                    
                    # Check page height to see if we can scroll more
                    page_height = driver.execute_script("return document.body.scrollHeight")
                    current_position = driver.execute_script("return window.pageYOffset + window.innerHeight")
                    
                    if current_position >= page_height - 200:
                        print(f"   [SCROLL] Reached bottom of page while looking for article {article_idx + 1}")
                        break
        
        # If article not found after all scrolling attempts
        if not found_this_article:
            consecutive_not_found += 1
            print(f"   [SCAN] Article {article_idx + 1} not found after scrolling (consecutive misses: {consecutive_not_found})")
            
            # Check if we've definitely reached the end
            page_height = driver.execute_script("return document.body.scrollHeight")
            current_position = driver.execute_script("return window.pageYOffset + window.innerHeight")
            
            if current_position >= page_height - 200:
                print(f"   [SCAN] Reached end of page at article {article_idx}")
                break
            
            # If too many consecutive misses, stop
            if consecutive_not_found >= max_consecutive_not_found:
                print(f"   [SCAN] Stopping - {consecutive_not_found} consecutive articles not found")
                break
            
            # Move to next article
            article_idx += 1
            continue
        
        # Article found - process it
        try:
            # Scroll article into view
            try:
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", heading_element)
                time.sleep(0.3)
            except:
                pass
            
            # Extract title
            try:
                title = heading_element.text.strip()
                if not title:
                    title = heading_element.get_attribute("textContent").strip()
            except:
                title = ""
            
            # Extract date
            try:
                date_text = date_element.text.strip()
                if not date_text:
                    date_text = date_element.get_attribute("textContent").strip()
            except:
                date_text = ""
            
            # Get article link
            try:
                article_link = heading_element.get_attribute('href')
            except:
                print(f"   Article {article_idx + 1}: Could not find link, skipping")
                article_idx += 1
                continue

            print(f"\n   Article {article_idx + 1}: {title[:60]}...")
            print(f"     Link: {article_link}")
            print(f"     Date text: {date_text}")

            # Parse the date
            article_date = None
            if date_text:
                article_date = parse_dinamina_date(date_text)
                if article_date:
                    print(f"     Parsed date: {article_date.strftime('%Y-%m-%d')}")
                else:
                    print(f"     Could not parse date '{date_text}'")

            # Check if article is in date range
            if article_date is None:
                print(f"     No date found, skipping article")
                consecutive_outside_range += 1
            elif is_article_in_date_range(article_date, start_date, end_date):
                print(f"     Article is in date range! Attempting to extract full content...")

                original_window = driver.current_window_handle
                opened_new_tab = False
                
                final_date = article_date
                final_title = title
                final_image = ""
                final_description = ""
                extraction_successful = False
                
                try:
                    print(f"     [NEW TAB] Attempting to open article in new tab: {article_link}")
                    
                    windows_before = set(driver.window_handles)
                    driver.execute_script("window.open(arguments[0], '_blank');", article_link)
                    time.sleep(2)
                    
                    windows_after = set(driver.window_handles)
                    new_windows = windows_after - windows_before
                    
                    if new_windows:
                        new_window = new_windows.pop()
                        driver.switch_to.window(new_window)
                        opened_new_tab = True
                        print(f"     [NEW TAB] ✓ Switched to new tab")
                        
                        print(f"     [NEW TAB] Waiting for page to load...")
                        time.sleep(3)
                        
                        current_url = driver.current_url
                        print(f"     [NEW TAB] Current URL: {current_url}")
                    else:
                        print(f"     [WARNING] New tab did not open, trying direct navigation...")
                        driver.set_page_load_timeout(30)
                        driver.get(article_link)
                        opened_new_tab = False
                        time.sleep(3)

                    # Extract content
                    print(f"     [EXTRACT] Starting content extraction (30s timeout)...")
                    try:
                        article_content = extract_with_timeout(driver, timeout_seconds=30)
                        
                        if article_content is None:
                            print(f"     [TIMEOUT] Extraction exceeded 30 seconds")
                        else:
                            print(f"     [EXTRACT] Content extraction completed")
                            print(f"     [EXTRACT] Description length: {len(article_content.get('description', ''))}")

                            final_date = article_content['date_published'] if article_content.get('date_published') else article_date
                            final_title = article_content['title'] if article_content.get('title') else title
                            final_image = article_content['image_url'] if article_content.get('image_url') else ""
                                    
                            extracted_desc = article_content.get('description', '').strip()
                            if extracted_desc and len(extracted_desc) > 0:
                                final_description = extracted_desc
                                extraction_successful = True
                                print(f"     ✓ Using extracted full article text ({len(final_description)} chars)")
                            else:
                                print(f"     ⚠ Extracted description is empty")
                    except Exception as extract_error:
                        print(f"     [EXTRACT] ✗ Error during extraction: {extract_error}")
                    
                    # Return to list page
                    if opened_new_tab:
                        print(f"     [CLOSE TAB] Closing article tab...")
                        try:
                            driver.close()
                            driver.switch_to.window(original_window)
                            time.sleep(1)
                        except Exception as close_error:
                            print(f"     [WARNING] Error closing tab: {close_error}")
                            driver.get(list_url)
                            time.sleep(2)
                    else:
                        print(f"     [BACK] Navigating back...")
                        try:
                            driver.set_page_load_timeout(10)
                            driver.back()
                            time.sleep(2)
                        except Exception as back_error:
                            print(f"     [WARNING] Back() failed: {back_error}")
                            driver.get(list_url)
                            time.sleep(3)

                except Exception as e:
                    print(f"     ✗ Error during navigation/extraction: {e}")
                    try:
                        if opened_new_tab:
                            driver.close()
                            driver.switch_to.window(original_window)
                        
                        if driver.current_url != list_url:
                            driver.get(list_url)
                            time.sleep(3)
                    except:
                        pass
                
                # Save article
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
                articles_in_range += 1
                consecutive_outside_range = 0

                if extraction_successful:
                    print(f"     ✓ Article saved with full extracted content")
                else:
                    print(f"     ✓ Article saved with list page data")
            elif article_date.date() > end_date:
                print(f"     [SKIP] Article is newer than target range: {article_date.strftime('%Y-%m-%d')}")
                articles_outside_range += 1
            else:
                # Article is older than target range
                article_date_str = article_date.strftime('%Y-%m-%d')
                print(f"     [SKIP] Article is older than target range: {article_date_str}")
                articles_outside_range += 1
                consecutive_outside_range += 1

                # Check if article is too old
                days_before_range = (start_date - article_date.date()).days
                if days_before_range > 2:
                    print(f"     Article is {days_before_range} days before target range - stopping")
                    break

            # Check if we should stop due to consecutive articles outside range
            if consecutive_outside_range >= max_consecutive_outside:
                print(f"\n   Stopping: {max_consecutive_outside} consecutive articles outside date range")
                break

        except Exception as e:
            print(f"     Error with article {article_idx + 1}: {e}")
            import traceback
            traceback.print_exc()
        
        # Move to next article
        article_idx += 1
    
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
    
    print(f"[INFO] Starting Dinamina scraper (Local news)...")
    
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
        
        print(f"[INFO] Starting undetected Chrome...")
        
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
        print(f"[INFO] Starting Chrome browser...")
        driver = webdriver.Chrome(service=service, options=chrome_options)
        print(f"[INFO] Chrome browser started successfully")
        
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        print(f"[INFO] Stealth settings applied")
    
    driver.set_page_load_timeout(60)
    print(f"[INFO] Page load timeout set to 60 seconds")

    categories = ["local", "politics", "editorial", "sports", "features", "business", "world"]
    scraped_urls = set()
    all_articles = []
    total_articles_in_range = 0
    total_articles_outside_range = 0
    
    for category in categories:
        cat_url = f"https://www.dinamina.lk/category/{category}/"
        print(f"\n[INFO] === Processing category: {category} ({cat_url}) ===")
        
        try:
            articles, page_in_range, page_outside_range = process_articles_from_page(
                driver, cat_url, start_date, end_date
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
            print(f"[INFO] Category '{category}': Found {len(unique_articles)} unique articles in range")
        except Exception as e:
            print(f"  [ERROR] Error processing category {category}: {e}")
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
    os.makedirs('data', exist_ok=True)
    
    import os
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    data_dir = os.path.join(project_root, 'data')
    os.makedirs(data_dir, exist_ok=True)
    json_filename = os.path.join(data_dir, 'dinamina_latest_news.json')

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

        run_incremental_for_module("dinamina_selenium_json")
        sys.exit(0)

    if len(sys.argv) >= 3:
        try:
            start_date = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
            end_date = datetime.strptime(sys.argv[2], "%Y-%m-%d").date()
            
            main(start_date, end_date)
        except ValueError as e:
            print(f"[ERROR] Invalid date format. Use YYYY-MM-DD. Error: {e}")
            print("[INFO] Example: python dinamina_selenium_json.py 2026-02-02 2026-02-03")
    else:
        main()