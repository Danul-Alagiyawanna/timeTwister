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
import os

from incremental import (
    incremental_fetch_limit,
    is_incremental_mode,
    load_incremental_boundary_links,
    merge_and_save,
    normalize_link,
    reached_incremental_limit,
    should_stop_at_feed_item,
)
from incremental_runner import article_from_content, data_json_path, run_incremental_scraper
from incremental_links import collect_divaina_breaking_links, collect_divaina_main_links

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError:
    pass

BASE_URL = "https://www.divaina.lk/category/breaking-news"
MAIN_NEWS_URL = "https://www.divaina.lk/category/main-news"
PROVINCIAL_NEWS_URL = "https://www.divaina.lk/category/provincial-news"

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
                
                # Try multiple selectors for Divaina
                selectors = [
                    "article .entry-content",
                    ".entry-content",
                    "article .post-content",
                    ".post-content",
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

def parse_divaina_date(date_text):
    """Parse Divaina date format."""
    try:
        # Format: "January 30, 2026"
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

def process_main_news_page(driver, list_url, start_date, end_date):
    """Process articles from main news page with different structure and 'Show More' button."""
    print(f"\n[INFO] Processing main news from: {list_url}")
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
    max_consecutive_outside = 2
    
    print(f"   Scanning for articles using main news XPath patterns with progressive scrolling...")

    # XPath patterns for main news (different structure)
    # First article has a different pattern
    first_article_patterns = {
        'heading': "/html/body/div/div[2]/div/div/div[1]/div/div[1]/div/div/div/div/div/div[2]/div/h2/a",
        'date': "/html/body/div/div[2]/div/div/div[1]/div/div[1]/div/div/div/div/div/div[2]/div/div/div/div[2]/time"
    }
    
    # Rest of the articles follow a pattern with div[N]
    article_list = []
    
    # Try to get the first article
    try:
        heading_element = driver.find_element(By.XPATH, first_article_patterns['heading'])
        article_list.append((0, heading_element, first_article_patterns['date']))
        print(f"   [SCAN] Found first article (special pattern)")
    except Exception as e:
        print(f"   [SCAN] Could not find first article: {e}")
    
    # Get remaining articles with progressive scrolling and "Show More" clicking
    max_articles = 100
    show_more_xpath = "/html/body/div/div[2]/div/div/div[2]/div/div/div/div/div[2]/a/span"
    show_more_clicks = 0
    max_show_more_clicks = 10
    consecutive_not_found = 0
    
    i = 1
    while i <= max_articles:
        try:
            # Articles 1-5 use one pattern, articles 6+ use a different pattern
            if i <= 5:
                heading_xpath = f"/html/body/div/div[2]/div/div/div[1]/div/div[2]/div/div/div/div[{i}]/h2/a"
                date_xpath = f"/html/body/div/div[2]/div/div/div[1]/div/div[2]/div/div/div/div[{i}]/div[2]/div/div[2]/time"
            else:
                # Articles 6+ have a different structure: div[1]/div[N] where N = i-5
                article_offset = i - 5
                heading_xpath = f"/html/body/div/div[2]/div/div/div[2]/div/div/div/div/div[1]/div[{article_offset}]/h2/a"
                # Date xpath for articles 6+ (need to find the correct pattern)
                date_xpath = f"/html/body/div/div[2]/div/div/div[2]/div/div/div/div/div[1]/div[{article_offset}]/div[2]/div/div[2]/time"
            
            # Try to find article with progressive scrolling
            heading_element = None
            for scroll_attempt in range(5):
                try:
                    heading_element = driver.find_element(By.XPATH, heading_xpath)
                    article_list.append((i, heading_element, date_xpath))
                    print(f"   [SCAN] Found article {i}")
                    consecutive_not_found = 0  # Reset counter when article is found
                    break
                except:
                    if scroll_attempt < 4:
                        # Scroll down to look for more content
                        driver.execute_script(f"window.scrollBy(0, 500);")
                        time.sleep(0.6)
            
            # If article found, move to next article
            if heading_element:
                i += 1
                continue
            
            # Article not found after scrolling
            consecutive_not_found += 1
            print(f"   [SCAN] Article {i} not found after scrolling (consecutive misses: {consecutive_not_found})")
            
            # Try clicking "Show More" if we've missed articles
            if consecutive_not_found >= 1 and show_more_clicks < max_show_more_clicks:
                try:
                    # Scroll down a bit more to find the "Show More" button
                    driver.execute_script(f"window.scrollBy(0, 400);")
                    time.sleep(0.5)
                    
                    show_more_button = driver.find_element(By.XPATH, show_more_xpath)
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", show_more_button)
                    time.sleep(0.5)
                    show_more_button.click()
                    show_more_clicks += 1
                    print(f"   [SHOW MORE] Clicked 'Show More' button (#{show_more_clicks}) - loading more articles...")
                    time.sleep(4)  # Wait for new content to load
                    
                    # Reset consecutive counter and continue scanning from current position
                    consecutive_not_found = 0
                    
                    # Scroll back up a bit to start finding articles
                    driver.execute_script(f"window.scrollBy(0, -300);")
                    time.sleep(0.5)
                    
                    # Don't increment i - try finding this article again
                    continue
                    
                except Exception as show_more_error:
                    print(f"   [SHOW MORE] Button not available: {show_more_error}")
                    # If "Show More" button is not available, stop
                    if consecutive_not_found >= 2:
                        print(f"   [SCAN] No more articles available (last found: article {i-consecutive_not_found})")
                        break
            
            # If we've missed too many consecutive articles, stop scanning
            if consecutive_not_found >= 3:
                print(f"   [SCAN] Stopping - {consecutive_not_found} consecutive articles not found")
                break
            
            # Move to next article
            i += 1
                
        except Exception as e:
            print(f"   [SCAN] Error with article {i}: {e}")
            i += 1
            continue
    
    # Scroll back to top
    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(0.5)
    
    print(f"   Processing {len(article_list)} article elements...")
    
    # Process each article
    for idx, article_data in enumerate(article_list, 1):
        try:
            article_idx, heading_element, date_xpath = article_data
            
            # Scroll article into view
            try:
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", heading_element)
                time.sleep(0.3)
            except:
                pass
            
            # Extract title
            try:
                title = heading_element.text.strip()
            except:
                title = ""
            
            # Extract date
            try:
                date_element = driver.find_element(By.XPATH, date_xpath)
                date_text = date_element.text.strip()
            except:
                date_text = ""
            
            # Get article link
            try:
                article_link = heading_element.get_attribute('href')
            except:
                print(f"   Article {idx}: Could not find link, skipping")
                continue

            print(f"\n   Article {idx}: {title[:60]}...")
            print(f"     Link: {article_link}")
            print(f"     Date text: {date_text}")

            # Parse the date
            article_date = None
            if date_text:
                article_date = parse_divaina_date(date_text)
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
            else:
                article_date_str = article_date.strftime('%Y-%m-%d')
                print(f"     [SKIP] Article outside date range: {article_date_str}")
                articles_outside_range += 1
                consecutive_outside_range += 1

                days_before_range = (start_date - article_date.date()).days
                if days_before_range > 2:
                    print(f"     Article is {days_before_range} days before target range - stopping")
                    break

            if consecutive_outside_range >= max_consecutive_outside:
                print(f"\n   Stopping: {max_consecutive_outside} consecutive articles outside date range")
                break

        except Exception as e:
            print(f"     Error with article {idx}: {e}")
            continue
    
    print(f"\n   Page summary: {articles_in_range} in range, {articles_outside_range} outside range")
    return articles_found, articles_in_range, articles_outside_range

def process_articles_from_list_page(driver, list_url, start_date, end_date):
    """Process articles from a single list page using XPath navigation."""
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
        print(f"[DEBUG] Page source length: {len(driver.page_source)} characters")
    except Exception as e:
        print(f"[ERROR] Failed to navigate to page: {e}")
        import traceback
        traceback.print_exc()
        return [], 0, 0

    articles_found = []
    articles_in_range = 0
    articles_outside_range = 0
    consecutive_outside_range = 0
    max_consecutive_outside = 2

    print(f"   Scanning for articles using progressive scrolling and XPath...")

    # Progressive scrolling approach - scroll and find articles incrementally
    article_elements = []
    max_articles = 50
    current_scroll_position = 0
    scroll_increment = 600
    
    for i in range(1, max_articles + 1):
        try:
            base_xpath = f"/html/body/div/div[2]/div/div/div/div/div/div/div/div/div[{i}]"
            heading_xpath = f"{base_xpath}/div/div[2]/h2/a"
            
            # Try to find the article
            heading_element = None
            attempts = 0
            max_attempts = 3
            
            while not heading_element and attempts < max_attempts:
                try:
                    heading_element = driver.find_element(By.XPATH, heading_xpath)
                    # Article found, add to list
                    article_elements.append((i, heading_element, base_xpath))
                    print(f"   [SCAN] Found article {i}")
                    break
                except:
                    # Article not visible yet, scroll down more
                    if attempts < max_attempts - 1:
                        current_scroll_position += scroll_increment
                        driver.execute_script(f"window.scrollTo(0, {current_scroll_position});")
                        time.sleep(1)
                        attempts += 1
                        print(f"   [SCROLL] Scrolled to {current_scroll_position}px looking for article {i}")
                    else:
                        # Couldn't find this article after scrolling
                        break
            
            # If we couldn't find this article, check if we've scrolled past the content
            if not heading_element:
                # Check if we've reached the bottom
                page_height = driver.execute_script("return document.body.scrollHeight")
                if current_scroll_position >= page_height - 1000:
                    print(f"   [SCAN] Reached end of page at article {i-1}")
                    break
        except Exception as e:
            print(f"   [SCAN] Error scanning for article {i}: {e}")
            continue
    
    # Scroll back to top
    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(0.5)
    
    print(f"   Processing {len(article_elements)} article elements...")
    
    # Process each article
    for idx, article_el in enumerate(article_elements, 1):
        try:
            # Handle different article element formats
            if isinstance(article_el, tuple):
                # XPath-based: (index, heading_element, base_xpath)
                i, heading_element, base_xpath = article_el
                date_xpath = f"{base_xpath}/div/div[2]/div/div/div[2]/time"
                
                # Scroll the article into view
                try:
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", heading_element)
                    time.sleep(0.3)
                except:
                    pass
                
                try:
                    title = heading_element.text.strip()
                except:
                    title = ""

                try:
                    date_element = driver.find_element(By.XPATH, date_xpath)
                    date_text = date_element.text.strip()
                except:
                    date_text = ""

                try:
                    article_link = heading_element.get_attribute('href')
                except:
                    print(f"   Article {idx}: Could not find link, skipping")
                    continue
            else:
                # CSS-based: article element
                try:
                    # Scroll into view
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", article_el)
                    time.sleep(0.3)
                except:
                    pass
                
                try:
                    # Find heading and link
                    heading_element = article_el.find_element(By.CSS_SELECTOR, "h2 a, h3 a, .entry-title a")
                    title = heading_element.text.strip()
                    article_link = heading_element.get_attribute('href')
                except Exception as e:
                    print(f"   Article {idx}: Could not find heading/link, skipping: {e}")
                    continue
                
                try:
                    # Find date
                    date_element = article_el.find_element(By.CSS_SELECTOR, "time, .date, .entry-date")
                    date_text = date_element.text.strip()
                except:
                    date_text = ""

            print(f"\n   Article {idx}: {title[:60]}...")
            print(f"     Link: {article_link}")
            print(f"     Date text: {date_text}")

            # Parse the date
            article_date = None
            if date_text:
                article_date = parse_divaina_date(date_text)
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
                        if current_url != article_link and article_link not in current_url:
                            print(f"     [WARNING] URL mismatch in new tab")
                            driver.close()
                            driver.switch_to.window(original_window)
                            raise Exception("URL mismatch in new tab")
                        
                        print(f"     [NEW TAB] Current URL: {current_url}")
                        print(f"     [NEW TAB] Page title: {driver.title[:80]}...")
                    else:
                        print(f"     [WARNING] New tab did not open, trying direct navigation...")
                        try:
                            driver.set_page_load_timeout(30)
                            driver.get(article_link)
                            opened_new_tab = False
                            print(f"     [NAVIGATE] Direct navigation complete")
                            time.sleep(3)
                        except Exception as nav_error:
                            print(f"     [NAVIGATE] ✗ Navigation error: {nav_error}")
                            raise

                    # Extract content with timeout
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
                            try:
                                driver.set_page_load_timeout(30)
                                driver.get(list_url)
                                time.sleep(3)
                            except Exception as reload_error:
                                print(f"     [ERROR] Could not reload list page: {reload_error}")

                except Exception as e:
                    print(f"     ✗ Error during navigation/extraction: {e}")
                    try:
                        if opened_new_tab:
                            try:
                                driver.close()
                                driver.switch_to.window(original_window)
                            except:
                                pass
                        
                        if driver.current_url != list_url:
                            driver.get(list_url)
                            time.sleep(3)
                    except Exception as recover_error:
                        print(f"     [RECOVER] Error recovering: {recover_error}")
                
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
            else:
                article_date_str = article_date.strftime('%Y-%m-%d')
                print(f"     [SKIP] Article outside date range: {article_date_str}")
                articles_outside_range += 1
                consecutive_outside_range += 1

                days_before_range = (start_date - article_date.date()).days
                if days_before_range > 2:
                    print(f"     Article is {days_before_range} days before target range - stopping")
                    break

            if consecutive_outside_range >= max_consecutive_outside:
                print(f"\n   Stopping: {max_consecutive_outside} consecutive articles outside date range")
                break

        except Exception as e:
            print(f"     Error with article {idx}: {e}")
            continue
    
    print(f"\n   Page summary: {articles_in_range} in range, {articles_outside_range} outside range")
    return articles_found, articles_in_range, articles_outside_range

def main(start_date=None, end_date=None, page_type="all"):
    """Main function."""
    
    if not start_date or not end_date:
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=1)
        print(f"[DATE] No date range provided, using default: {start_date} to {end_date}")
    else:
        print(f"[DATE] Scraping articles from {start_date} to {end_date}")
    
    print(f"[INFO] Starting Divaina scraper ({page_type} news)...")
    
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

    all_articles = []
    total_articles_in_range = 0
    total_articles_outside_range = 0
    max_pages = 10
    consecutive_empty_pages = 0
    max_consecutive_empty = 3
    
    print(f"\n[INFO] Processing articles...")
    
    # Process news pages based on type requested
    pages_to_scrape = []
    
    if page_type == "main":
        pages_to_scrape = ["main"]
    elif page_type == "breaking":
        pages_to_scrape = ["breaking"]
    elif page_type == "provincial":
        pages_to_scrape = ["provincial"]
    else:
        # By default, scrape all three
        pages_to_scrape = ["main", "breaking", "provincial"]
    
    for current_page_type in pages_to_scrape:
        if current_page_type == "main":
            print(f"\n[INFO] === Processing MAIN NEWS ===")
            # Main news page - single page with "Show More" button
            try:
                articles, page_in_range, page_outside_range = process_main_news_page(
                    driver, MAIN_NEWS_URL, start_date, end_date
                )
                
                all_articles.extend(articles)
                total_articles_in_range += page_in_range
                total_articles_outside_range += page_outside_range
            except Exception as e:
                print(f"  [ERROR] Error processing main news page: {e}")
        elif current_page_type == "provincial":
            print(f"\n[INFO] === Processing PROVINCIAL NEWS ===")
            # Provincial news page - similar structure to main news
            try:
                articles, page_in_range, page_outside_range = process_main_news_page(
                    driver, PROVINCIAL_NEWS_URL, start_date, end_date
                )
                
                all_articles.extend(articles)
                total_articles_in_range += page_in_range
                total_articles_outside_range += page_outside_range
            except Exception as e:
                print(f"  [ERROR] Error processing provincial news page: {e}")
        else:
            print(f"\n[INFO] === Processing BREAKING NEWS ===")
            # Breaking news - multiple pages with pagination
            for page_num in range(max_pages):
                if page_num == 0:
                    list_url = BASE_URL
                else:
                    # Divaina pagination: /category/breaking-news/page/2
                    list_url = f"{BASE_URL}/page/{page_num + 1}"
                
                print(f"\n[INFO] Page {page_num + 1}:")
                
                try:
                    articles, page_in_range, page_outside_range = process_articles_from_list_page(
                        driver, list_url, start_date, end_date
                    )
                    
                    all_articles.extend(articles)
                    total_articles_in_range += page_in_range
                    total_articles_outside_range += page_outside_range
                    
                    if page_in_range == 0:
                        consecutive_empty_pages += 1
                        print(f"  [WARNING] No articles in range on this page ({consecutive_empty_pages}/{max_consecutive_empty})")
                        if consecutive_empty_pages >= max_consecutive_empty:
                            print(f"  [ERROR] Stopping: {max_consecutive_empty} consecutive pages with no articles in date range")
                            break
                    else:
                        consecutive_empty_pages = 0
                    
                    time.sleep(2)
                    
                except Exception as e:
                    print(f"  [ERROR] Error processing page {page_num + 1}: {e}")
                    continue
    
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
    
    # Always save to the same file - divaina_latest_news.json
    import os
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    data_dir = os.path.join(project_root, 'data')
    os.makedirs(data_dir, exist_ok=True)
    json_filename = os.path.join(data_dir, 'divaina_latest_news.json')

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

_DIVAINA_ARTICLE_RE = re.compile(
    r"https?://(?:www\.)?divaina\.lk/(breaking-news|main-news|provincial-news|article)/\d+",
    re.I,
)


def _chrome_major_version() -> int | None:
    import subprocess

    for cmd in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        try:
            out = subprocess.check_output([cmd, "--version"], text=True, timeout=10)
            m = re.search(r"(\d+)\.", out)
            if m:
                return int(m.group(1))
        except Exception:
            continue
    return None


def _create_divaina_driver():
    """UC with version_main retry — required on GHA (plain Selenium hits Cloudflare)."""
    if USE_UNDETECTED:
        print("[INFO] Using undetected-chromedriver...")
        options = uc.ChromeOptions()
        options.page_load_strategy = "eager"
        options.add_experimental_option(
            "prefs", {"profile.default_content_setting_values": {"popups": 1}}
        )
        if os.getenv("CI"):
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")

        major = _chrome_major_version()
        attempts: list[tuple[str, dict]] = [("auto", {})]
        if major:
            attempts.insert(0, (f"version_main={major}", {"version_main": major}))

        last_err: Exception | None = None
        for label, kwargs in attempts:
            try:
                print(f"[INFO] Starting undetected Chrome ({label})...")
                driver = uc.Chrome(options=options, use_subprocess=True, **kwargs)
                print("[INFO] Undetected Chrome started successfully")
                driver.execute_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
                )
                return driver
            except Exception as e:
                last_err = e
                print(f"[WARNING] UC failed ({label}): {str(e)[:200]}")

        print(f"[WARNING] UC unavailable: {last_err}")

    print("[INFO] Falling back to regular Selenium...")
    chrome_options = Options()
    chrome_options.page_load_strategy = "eager"
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option("useAutomationExtension", False)
    chrome_options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return driver


def _http_get(url: str, **kwargs):
    """curl_cffi first (GHA Cloudflare), then requests."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
        "Accept-Language": "en-US,en;q=0.9,si;q=0.8",
        "Referer": "https://www.divaina.lk/",
    }
    import requests as req

    for profile in ("chrome124", "chrome120", "safari17_0", "firefox133"):
        try:
            from curl_cffi import requests as cf_req  # type: ignore

            r = cf_req.get(
                url, impersonate=profile, timeout=25, headers=headers, **kwargs
            )
            if r.status_code == 200 and len(r.text) > 200:
                print(f"[INFO] curl_cffi ({profile}) OK for {url}")
                return r
            print(f"[WARN] curl_cffi ({profile}) {r.status_code} for {url}")
        except ImportError:
            break
        except Exception as e:
            print(f"[WARN] curl_cffi ({profile}): {e}")

    try:
        r = req.get(url, timeout=20, allow_redirects=True, headers=headers, **kwargs)
        if r.status_code == 200 and len(r.text) > 200:
            return r
        print(f"[WARN] requests {r.status_code} for {url}")
    except Exception as e:
        print(f"[WARN] requests failed: {e}")
    return None


def _is_divaina_article_url(link: str) -> bool:
    return bool(link and _DIVAINA_ARTICLE_RE.search(link))


def _html_to_plain(html: str) -> str:
    if not html:
        return ""
    try:
        text = BeautifulSoup(html, "html.parser").get_text(separator="\n", strip=True)
    except Exception:
        text = html.strip()
    cut = re.split(
        r"The post .+ appeared first on ",
        text,
        maxsplit=1,
        flags=re.I,
    )
    return cut[0].strip()


def _first_img_from_html(html: str) -> str:
    if not html:
        return ""
    for pattern in (
        r'<img[^>]+src=["\']([^"\']+)["\']',
        r'<img[^>]+data-src=["\']([^"\']+)["\']',
    ):
        m = re.search(pattern, html, re.I)
        if m:
            return m.group(1).strip()
    return ""


def _build_divaina_article_row(
    *,
    title: str,
    link: str,
    date_str: str,
    body_text: str,
    image_url: str = "",
    date_source: str = "",
) -> dict:
    return {
        "title": title,
        "link": link,
        "summary": body_text,
        "description": body_text,
        "date": date_str,
        "image_url": image_url,
        "date_source": date_source or f"RSS: {date_str}",
    }


def _parse_divaina_rss(xml_text: str) -> list[dict]:
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime

    ns = {
        "content": "http://purl.org/rss/1.0/modules/content/",
        "dc": "http://purl.org/dc/elements/1.1/",
        "media": "http://search.yahoo.com/mrss/",
    }
    articles: list[dict] = []
    root = ET.fromstring(xml_text)
    items = root.findall("./channel/item") or root.findall(".//item")
    for item in items:
        link = (item.findtext("link") or "").strip()
        if not _is_divaina_article_url(link):
            continue
        title = (item.findtext("title") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if pub_date:
            try:
                date_str = parsedate_to_datetime(pub_date).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                pass

        desc_html = (item.findtext("description") or "").strip()
        content_el = item.find("content:encoded", ns)
        content_html = (content_el.text or "").strip() if content_el is not None else ""
        body_text = _html_to_plain(content_html) or _html_to_plain(desc_html)

        image_url = ""
        thumb = item.find("media:thumbnail", ns)
        if thumb is not None and thumb.get("url"):
            image_url = thumb.get("url", "").strip()
        enclosure = item.find("enclosure")
        if not image_url and enclosure is not None and enclosure.get("type", "").startswith(
            "image"
        ):
            image_url = (enclosure.get("url") or "").strip()
        if not image_url:
            image_url = _first_img_from_html(content_html) or _first_img_from_html(desc_html)

        articles.append(
            _build_divaina_article_row(
                title=title,
                link=link,
                date_str=date_str,
                body_text=body_text,
                image_url=image_url,
                date_source=f"RSS: {date_str}",
            )
        )
    return articles


def _wait_cloudflare_clear(driver, max_wait: int = 60) -> bool:
    deadline = time.time() + max_wait
    while time.time() < deadline:
        title = (driver.title or "").lower()
        if "just a moment" not in title and "attention required" not in title:
            return True
        print("[INFO] Waiting for Cloudflare challenge...")
        time.sleep(4)
    return False


def _extract_rss_xml_from_html(page_source: str) -> str:
    m = re.search(r"<\?xml[^>]*\?>.*?</rss>", page_source, re.DOTALL | re.I)
    if m:
        return m.group(0)
    idx = page_source.lower().find("<rss")
    return page_source[idx:] if idx >= 0 else ""


def _fetch_divaina_feed_via_selenium() -> list[dict]:
    print("[INFO] Fetching Divaina RSS via browser (undetected Chrome)...")
    driver = _create_divaina_driver()
    driver.set_page_load_timeout(90)
    try:
        driver.get("https://www.divaina.lk/feed/")
        if not _wait_cloudflare_clear(driver, 75):
            print("[WARN] Cloudflare still blocking /feed/ in browser")
            return []
        xml = _extract_rss_xml_from_html(driver.page_source)
        if not xml or "<item" not in xml:
            print(f"[WARN] Browser feed page is not RSS (title={driver.title[:50]!r})")
            return []
        items = _parse_divaina_rss(xml)
        valid = [a for a in items if _is_divaina_article_url(a.get("link", ""))]
        print(f"[INFO] Browser RSS parsed {len(valid)} article URL(s)")
        return valid
    except Exception as e:
        print(f"[ERROR] Browser feed fetch failed: {e}")
        return []
    finally:
        try:
            driver.quit()
        except Exception:
            pass


def _fetch_divaina_feed_articles() -> list[dict]:
    for feed_url in ("https://www.divaina.lk/feed/", "https://divaina.lk/feed/"):
        resp = _http_get(feed_url)
        if not resp:
            continue
        if "just a moment" in resp.text.lower()[:8000]:
            print(f"[WARN] Cloudflare interstitial on {feed_url}")
            continue
        print(f"[INFO] RSS fetched {feed_url} ({len(resp.text)} bytes)")
        try:
            items = _parse_divaina_rss(resp.text)
            valid = [a for a in items if _is_divaina_article_url(a.get("link", ""))]
            if valid:
                print(f"[INFO] RSS parsed {len(valid)} article URL(s)")
                return valid
            print(f"[WARN] RSS had {len(items)} items but 0 article URLs")
        except Exception as e:
            print(f"[WARN] RSS parse error: {e}")

    if os.getenv("CI", "").lower() in ("1", "true"):
        valid = _fetch_divaina_feed_via_selenium()
        if valid:
            return valid

    print("[WARN] Divaina /feed/ unavailable via HTTP and browser")
    return []


def main_incremental_rss() -> int:
    """Global checkpoint via site RSS (GHA — article pages block plain Selenium)."""
    json_filename = data_json_path("divaina_latest_news.json")
    checkpoint_link, known_previous = load_incremental_boundary_links(json_filename)
    bootstrap = not checkpoint_link
    max_articles = incremental_fetch_limit(bootstrap=bootstrap)

    if known_previous:
        print(
            f"[INCREMENTAL] Boundary set: {len(known_previous)} URL(s) "
            "(archive + checkpoint)"
        )

    print("[INCREMENTAL] Divaina — RSS feed order (newest first)")
    if bootstrap:
        print(f"[INCREMENTAL] No checkpoint; bootstrap max {max_articles} articles")
    else:
        print(
            f"[INCREMENTAL] Run safety cap: {max_articles} new articles "
            f"(checkpoint: {checkpoint_link[:70]}...)"
        )

    all_feed = _fetch_divaina_feed_articles()
    if not all_feed:
        print("[ERROR] No Divaina article URLs from RSS — keeping existing data")
        return -1

    new_articles: list[dict] = []
    seen_this_run: set[str] = set()
    cap_hit = False

    for i, art in enumerate(all_feed, 1):
        if cap_hit:
            break
        link = art["link"]
        norm = normalize_link(link)

        stop_reason = should_stop_at_feed_item(
            link,
            checkpoint_link=checkpoint_link,
            known_previous=known_previous,
        )
        if stop_reason:
            print(
                f"\n[INCREMENTAL] Reached boundary ({stop_reason}) — stopping."
            )
            print(f"             {link}")
            break

        if norm in seen_this_run:
            continue
        if not art.get("title") and not art.get("summary") and not art.get("description"):
            print(f"[SKIP] Empty row: {link[:80]}...")
            continue

        print(f"\n[INFO] New {i}: {link[:80]}...")
        new_articles.append(art)
        seen_this_run.add(norm)
        print(f"[+] {(art.get('title') or '')[:70]}")

        if reached_incremental_limit(len(new_articles), bootstrap=bootstrap):
            label = "Bootstrap" if bootstrap else "Run safety"
            print(f"[INCREMENTAL] {label} limit ({max_articles}) reached.")
            cap_hit = True
            break

    print(f"\n[INCREMENTAL] New articles: {len(new_articles)}")
    if new_articles:
        merge_and_save(json_filename, new_articles)
    else:
        print("[INCREMENTAL] No new articles — keeping existing data file unchanged")
    return len(new_articles)


def _fetch_divaina_article(driver, link: str) -> dict | None:
    driver.get(link)
    _wait_cloudflare_clear(driver, 75)
    time.sleep(2)
    meta = extract_with_timeout(driver, timeout_seconds=30)
    if not meta:
        return None
    return article_from_content(meta, link)


def main_incremental_selenium() -> int:
    pages = [
        ("breaking", BASE_URL),
        ("main", MAIN_NEWS_URL),
        ("provincial", PROVINCIAL_NEWS_URL),
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
        fetch_article=_fetch_divaina_article,
        create_driver=_create_divaina_driver,
        use_undetected=False,
    )


def main_incremental() -> int:
    """CI: RSS (HTTP → browser) then Selenium+xvfb; local: Selenium then RSS."""
    if os.getenv("CI", "").lower() in ("1", "true"):
        print("[INCREMENTAL] CI detected — Divaina RSS feed (HTTP / browser)")
        n = main_incremental_rss()
        if n < 0:
            print("[INCREMENTAL] RSS blocked — falling back to Selenium category scrape")
            return main_incremental_selenium()
        return n

    count = main_incremental_selenium()
    if count == 0:
        try:
            driver = _create_divaina_driver()
            driver.get(BASE_URL)
            time.sleep(2)
            cf = "just a moment" in (driver.title or "").lower()
            driver.quit()
        except Exception:
            cf = True
        if cf:
            print("[INCREMENTAL] Cloudflare detected — falling back to RSS")
            return main_incremental_rss()
    return count


if __name__ == "__main__":
    _scraper_dir = os.path.dirname(os.path.abspath(__file__))
    if _scraper_dir not in sys.path:
        sys.path.insert(0, _scraper_dir)

    if is_incremental_mode():
        main_incremental()
        sys.exit(0)

    if len(sys.argv) >= 3:
        try:
            start_date = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
            end_date = datetime.strptime(sys.argv[2], "%Y-%m-%d").date()
            
            # Check if third argument specifies page type
            page_type = sys.argv[3] if len(sys.argv) >= 4 else "all"
            
            if page_type == "main":
                print(f"[INFO] Scraping MAIN NEWS page")
                main(start_date, end_date, page_type="main")
            elif page_type == "breaking":
                print(f"[INFO] Scraping BREAKING NEWS page")
                main(start_date, end_date, page_type="breaking")
            elif page_type == "provincial":
                print(f"[INFO] Scraping PROVINCIAL NEWS page")
                main(start_date, end_date, page_type="provincial")
            else:
                print(f"[INFO] Scraping ALL NEWS pages (main + breaking + provincial)")
                main(start_date, end_date, page_type="all")
        except ValueError as e:
            print(f"[ERROR] Invalid date format. Use YYYY-MM-DD. Error: {e}")
            print("[INFO] Example: python divaina_selenium_json.py 2026-01-28 2026-01-29 [breaking|main|provincial|all]")
    else:
        main()