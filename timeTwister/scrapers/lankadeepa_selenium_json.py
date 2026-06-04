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
import os
import threading
from functools import wraps
import locale

from incremental import (
    get_section_checkpoint,
    incremental_fetch_limit,
    is_incremental_mode,
    load_known_links,
    normalize_link,
    reached_incremental_limit,
    save_replace_only,
    update_section_checkpoints,
)
from incremental_links import collect_lankadeepa_links
from incremental_runner import article_from_content, create_standard_driver, data_json_path

LANKADEEPA_CATEGORIES = [
    ("latest", "https://www.lankadeepa.lk/latest-news/1"),
    ("features", "https://www.lankadeepa.lk/features/2"),
    ("politics", "https://www.lankadeepa.lk/politics/13"),
    ("sports", "https://www.lankadeepa.lk/sports/7"),
    ("provincial", "https://www.lankadeepa.lk/provincial/9"),
    ("world", "https://www.lankadeepa.lk/world/8"),
    ("editorial", "https://www.lankadeepa.lk/editorial/15"),
]

# Set up UTF-8 encoding for console output (Windows fix)
import sys
if sys.platform == 'win32':
    try:
        # Try to set UTF-8 encoding for stdout
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8')
        if hasattr(sys.stderr, 'reconfigure'):
            sys.stderr.reconfigure(encoding='utf-8')
    except:
        pass  # If it fails, the try-except blocks around prints will handle it

BASE_URL = "https://www.lankadeepa.lk/latest-news/1"
FEATURES_URL = "https://www.lankadeepa.lk/features/2"
POLITICS_URL = "https://www.lankadeepa.lk/politics/13"

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

        # Extract publication date from meta tag
        date_published = None
        date_meta = soup.find('meta', attrs={'property': 'article:published_time'})
        if date_meta and date_meta.has_attr('content'):
            try:
                # Parse ISO format date: "2026-01-29T10:30:00+05:30"
                date_str = date_meta['content']
                # Remove timezone info for simpler parsing
                date_str_clean = date_str.split('+')[0].split('T')
                if len(date_str_clean) == 2:
                    date_published = datetime.strptime(f"{date_str_clean[0]} {date_str_clean[1]}", "%Y-%m-%d %H:%M:%S")
                    print(f"     Found exact date: {date_published}")
            except ValueError as e:
                print(f"     Error parsing date '{date_meta['content']}': {e}")
        else:
            print(f"     No article:published_time meta tag found")

        # Extract image URL from OpenGraph meta tag
        image_url = ""
        og_image = soup.find('meta', attrs={'property': 'og:image'})
        if og_image and og_image.has_attr('content'):
            image_url = og_image['content']
            print(f"     Found image: {image_url[:60]}...")

        # Extract full article text
        full_article_text = ""
        extraction_successful = False
        
        # Check elapsed time before starting extraction strategies
        if time.time() - start_time > max_elapsed_time:
            raise TimeoutError("Extraction timeout exceeded")
        
        # Strategy 1: Try to find the main article content area (Lankadeepa specific)
        if not extraction_successful:
            try:
                # Check elapsed time
                if time.time() - start_time > max_elapsed_time:
                    raise TimeoutError("Extraction timeout exceeded")
                
                # Wait for the article content to load
                print(f"     [EXTRACT] Waiting for article content...")
                article_body = None
                
                # Try Lankadeepa-specific selectors first
                selectors = [
                    ".article-body.sinhala-body",  # Specific to Lankadeepa
                    ".article-body",
                    "article .article-body",
                    ".article-content",
                    "article .a-content",
                    ".story-content",
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
                    # Check elapsed time
                    if time.time() - start_time > max_elapsed_time:
                        raise TimeoutError("Extraction timeout exceeded")
                    
                    # Wait a bit for dynamic content
                    time.sleep(1)
                    
                    # Find all paragraph tags
                    paragraphs = article_body.find_elements(By.TAG_NAME, "p")
                    print(f"     [DEBUG] Found {len(paragraphs)} <p> tags in article body")
                    
                    paragraph_texts = []
                    
                    # If no <p> tags found, try getting direct text content from the div
                    if len(paragraphs) == 0:
                        print(f"     [DEBUG] No <p> tags, trying to get text directly from div...")
                        try:
                            # Get all text from the div (this will include all nested text)
                            all_text = article_body.text.strip()
                            if all_text and len(all_text) > 50:
                                # Split by multiple newlines to get paragraph-like chunks
                                text_chunks = [chunk.strip() for chunk in all_text.split('\n') if chunk.strip()]
                                # Filter out very short chunks (likely navigation/metadata)
                                paragraph_texts = [chunk for chunk in text_chunks if len(chunk) > 20]
                                print(f"     [DEBUG] Got {len(paragraph_texts)} text chunks from div")
                                if len(paragraph_texts) > 0:
                                    for idx in range(min(3, len(paragraph_texts))):
                                        print(f"     [DEBUG]   Chunk {idx+1}: {paragraph_texts[idx][:100]}...")
                        except Exception as e:
                            print(f"     [DEBUG]   Error getting text from div: {e}")
                    else:
                        # Process <p> tags normally
                        for idx, p in enumerate(paragraphs):
                            try:
                                text = p.text.strip()
                                if text:
                                    paragraph_texts.append(text)
                                    if idx < 3:  # Print first 3 paragraphs for debugging
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
        
        # Strategy 2: Fallback - try BeautifulSoup with multiple strategies
        if not extraction_successful:
            try:
                # Try finding the article-body div specifically
                article_body_div = soup.find('div', class_='article-body')
                if not article_body_div:
                    article_body_div = soup.find('article')
                
                if article_body_div:
                    # First try to get <p> tags
                    paragraphs = article_body_div.find_all('p')
                    paragraph_texts = [p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)]
                    
                    # If no <p> tags, get all text and split by newlines
                    if not paragraph_texts:
                        print(f"     [DEBUG] No <p> tags in BeautifulSoup, getting text directly...")
                        all_text = article_body_div.get_text(separator='\n', strip=True)
                        text_chunks = [chunk.strip() for chunk in all_text.split('\n') if chunk.strip()]
                        # Filter out very short chunks
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

        # Extract title from the page
        title = ""
        og_title = soup.find('meta', attrs={'property': 'og:title'})
        if og_title and og_title.has_attr('content'):
            title = og_title['content']

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

def parse_sinhala_relative_date(date_text):
    """Parse Sinhala relative date formats like 'මිනිත්තු 13කට පෙර' (13 minutes ago)."""
    now = datetime.now()
    date_text_clean = date_text.strip()
    
    # Common Sinhala time units and their patterns
    # පෙර = ago, minute(s) = මිනිත්තු/මිනිත්, hour(s) = පැය/පැයකට, day(s) = දින/දිනකට
    
    # Extract numbers
    numbers = re.findall(r'\d+', date_text_clean)
    
    if not numbers:
        return None
    
    value = int(numbers[0])
    
    # Check for time units in Sinhala
    if 'මිනිත්' in date_text_clean or 'minute' in date_text_clean.lower():
        return now - timedelta(minutes=value)
    elif 'පැය' in date_text_clean or 'hour' in date_text_clean.lower():
        return now - timedelta(hours=value)
    elif 'දින' in date_text_clean or 'day' in date_text_clean.lower():
        return now - timedelta(days=value)
    elif 'සති' in date_text_clean or 'week' in date_text_clean.lower():
        return now - timedelta(weeks=value)
    elif 'මාස' in date_text_clean or 'month' in date_text_clean.lower():
        return now - timedelta(days=value*30)  # Approximate
    
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

    articles_found = []
    articles_in_range = 0
    articles_outside_range = 0
    consecutive_outside_range = 0
    max_consecutive_outside = 2

    print(f"   Scanning for articles using XPath patterns...")

    # Define XPath patterns for different article types on Lankadeepa
    # Based on the patterns you provided
    heading_xpaths = [
        "/html/body/section/div/div[1]/section/div[1]/div[1]/article/a/h2",
        "/html/body/section/div/div[1]/section/div[1]/div[2]/article/a/h2",
    ]
    
    date_xpaths = [
        "/html/body/section/div/div[1]/section/div[1]/div[1]/article/div/span",
        "/html/body/section/div/div[1]/section/div[1]/div[2]/article/div/span",
    ]
    
    # Additional articles (smaller cards)
    additional_article_count = 10  # Check for multiple smaller articles
    
    all_xpaths = []
    
    # Add the two large articles
    for i in range(len(heading_xpaths)):
        all_xpaths.append({
            'heading': heading_xpaths[i],
            'date': date_xpaths[i]
        })
    
    # Add smaller articles (pattern: div[2]/article[N])
    for i in range(1, additional_article_count + 1):
        all_xpaths.append({
            'heading': f"/html/body/section/div/div[1]/section/div[2]/article[{i}]/div[1]/a/h3",
            'date': f"/html/body/section/div/div[1]/section/div[2]/article[{i}]/div[1]/div/span"
        })

    for idx, xpath_set in enumerate(all_xpaths):
        try:
            heading_xpath = xpath_set['heading']
            date_xpath = xpath_set['date']

            # Try to find the heading element
            try:
                heading_element = driver.find_element(By.XPATH, heading_xpath)
            except:
                # Article doesn't exist, continue to next
                continue

            # Extract data from list page
            try:
                title = heading_element.text.strip()
            except:
                title = ""

            try:
                date_element = driver.find_element(By.XPATH, date_xpath)
                date_text = date_element.text.strip()
            except:
                date_text = ""

            # Get article link
            try:
                # The heading is inside an <a> tag
                if 'h2' in heading_xpath:
                    link_element = heading_element.find_element(By.XPATH, "..")  # Parent <a> tag
                else:  # h3
                    link_element = heading_element.find_element(By.XPATH, "..")  # Parent <a> tag
                article_link = link_element.get_attribute('href')
            except:
                print(f"   Article {idx+1}: Could not find link, skipping")
                continue

            # Use ASCII-safe printing to avoid Windows encoding issues
            try:
                print(f"\n   Article {idx+1}: {title[:60]}...")
            except UnicodeEncodeError:
                print(f"\n   Article {idx+1}: [Sinhala title]")
            print(f"     Link: {article_link}")
            try:
                print(f"     Date text: {date_text}")
            except UnicodeEncodeError:
                print(f"     Date text: [Sinhala date]")

            # Parse the date from the date text
            article_date = None
            if date_text:
                try:
                    # Try to parse Sinhala relative date format
                    article_date = parse_sinhala_relative_date(date_text)
                    
                    if article_date:
                        try:
                            print(f"     Parsed relative date: {article_date.strftime('%Y-%m-%d %H:%M:%S')} from '{date_text}'")
                        except UnicodeEncodeError:
                            print(f"     Parsed relative date: {article_date.strftime('%Y-%m-%d %H:%M:%S')}")
                    else:
                        # Try absolute date formats
                        # Common format: "27 January 2026"
                        try:
                            article_date = datetime.strptime(date_text.strip(), "%d %B %Y")
                            print(f"     Parsed date: {article_date.strftime('%Y-%m-%d')}")
                        except:
                            pass
                        
                        # Try other formats
                        if not article_date:
                            for fmt in ["%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y"]:
                                try:
                                    article_date = datetime.strptime(date_text.strip(), fmt)
                                    break
                                except:
                                    continue
                            
                            if article_date:
                                print(f"     Parsed date: {article_date.strftime('%Y-%m-%d')}")

                except Exception as e:
                    try:
                        print(f"     Could not parse date '{date_text}': {e}")
                    except UnicodeEncodeError:
                        print(f"     Could not parse date: {e}")

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
                final_description = ""  # No description on list page for Lankadeepa
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
                        # New tab didn't open, try direct navigation
                        print(f"     [WARNING] New tab did not open, trying direct navigation...")
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
                                    
                            # Use extracted full article text
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
                    
                    # Return to list page
                    if opened_new_tab:
                        # Close tab and switch back
                        print(f"     [CLOSE TAB] Closing article tab and returning to list page...")
                        try:
                            driver.close()
                            driver.switch_to.window(original_window)
                            time.sleep(1)
                        except Exception as close_error:
                            print(f"     [WARNING] Error closing tab: {close_error}, reloading list page...")
                            driver.get(list_url)
                            time.sleep(2)
                    else:
                        # Navigate back
                        print(f"     [BACK] Navigating back to list page...")
                        try:
                            driver.set_page_load_timeout(10)
                            driver.back()
                            time.sleep(2)
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
                        if opened_new_tab:
                            try:
                                all_windows = driver.window_handles
                                current_window = driver.current_window_handle
                                if len(all_windows) > 1:
                                    driver.close()
                                    if original_window in all_windows:
                                        driver.switch_to.window(original_window)
                                    else:
                                        remaining_windows = [w for w in all_windows if w != current_window]
                                        if remaining_windows:
                                            driver.switch_to.window(remaining_windows[0])
                                print(f"     [RECOVER] Closed article tab and returned to list page")
                            except Exception as tab_error:
                                print(f"     [RECOVER] Error closing tab: {tab_error}")
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
                            driver.get(list_url)
                            time.sleep(3)
                        except:
                            pass
                
                # Always save the article
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
                consecutive_outside_range = 0

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

            # Check if we should stop
            if consecutive_outside_range >= max_consecutive_outside:
                print(f"\n   Stopping: {max_consecutive_outside} consecutive articles outside date range")
                break

        except Exception as e:
            print(f"     Error with article {idx+1}: {e}")
            continue
    
    print(f"\n   Page summary: {articles_in_range} in range, {articles_outside_range} outside range")
    return articles_found, articles_in_range, articles_outside_range

def main(start_date=None, end_date=None, page_type="all"):
    """Main function with click-and-back navigation approach."""
    
    # Default to yesterday and today if no dates provided
    if not start_date or not end_date:
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=1)
        print(f"[DATE] No date range provided, using default: {start_date} to {end_date}")
    else:
        print(f"[DATE] Scraping articles from {start_date} to {end_date}")
    
    print(f"[INFO] Starting Lankadeepa scraper ({page_type})...")
    
    import os
    
    if USE_UNDETECTED:
        print("[INFO] Using undetected-chromedriver to bypass Cloudflare...")
        
        options = uc.ChromeOptions()
        options.page_load_strategy = 'eager'
        
        # Disable popup blocking to allow new tabs
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
        print("[WARNING] undetected-chromedriver not installed. Install with: pip install undetected-chromedriver")
        print("[INFO] Falling back to regular Selenium...")
        
        chrome_options = Options()
        chrome_options.page_load_strategy = 'eager'
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-blink-features=AutomationControlled')
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
        
        # Disable popup blocking to allow new tabs
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
        
        # Add stealth settings
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
        print(f"[INFO] Stealth settings applied")
    
    # Set page load timeout
    driver.set_page_load_timeout(60)
    print(f"[INFO] Page load timeout set to 60 seconds")

    all_articles = []
    total_articles_in_range = 0
    total_articles_outside_range = 0
    max_pages = 10
    
    CATEGORIES = {
        "latest": ("https://www.lankadeepa.lk/latest-news/1", "1"),
        "features": ("https://www.lankadeepa.lk/features/2", "2"),
        "politics": ("https://www.lankadeepa.lk/politics/13", "13"),
        "sports": ("https://www.lankadeepa.lk/sports/7", "7"),
        "provincial": ("https://www.lankadeepa.lk/provincial/9", "9"),
        "world": ("https://www.lankadeepa.lk/world/8", "8"),
        "editorial": ("https://www.lankadeepa.lk/editorial/15", "15")
    }

    # Determine which pages to scrape
    pages_to_scrape = []
    if page_type in CATEGORIES:
        pages_to_scrape = [page_type]
    else:
        # Default: scrape all
        pages_to_scrape = list(CATEGORIES.keys())
    
    print(f"\n[INFO] Processing articles page by page with click-and-back navigation...")
    scraped_urls = set()
    
    for current_page_type in pages_to_scrape:
        print(f"\n[INFO] === Processing {current_page_type.upper()} NEWS ===")
        
        consecutive_empty_pages = 0
        max_consecutive_empty = 3
        
        for page_num in range(max_pages):
            url_base, cat_id = CATEGORIES[current_page_type]
            if page_num == 0:
                list_url = url_base
            else:
                list_url = f"{url_base}/{page_num * 30}"
            
            print(f"\n[INFO] {current_page_type.capitalize()} - Page {page_num + 1}:")
            
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
                        print(f"  [ERROR] Stopping {current_page_type}: {max_consecutive_empty} consecutive pages with no articles in date range")
                        break
                else:
                    consecutive_empty_pages = 0
                
                # Brief delay between pages
                time.sleep(2)
                
            except Exception as e:
                print(f"  [ERROR] Error processing page {page_num + 1}: {e}")
                continue
    
    # Close the driver
    print(f"\n[INFO] Closing browser...")
    try:
        driver.quit()
        print(f"[INFO] Browser closed successfully")
    except Exception as e:
        print(f"[WARNING] Error closing browser (may be harmless): {e}")
        try:
            driver.close()
        except:
            pass
    
    print(f"\n[INFO] Final Results:")
    print(f"  [INFO] Articles in date range: {total_articles_in_range}")
    print(f"  [INFO] Articles outside range: {total_articles_outside_range}")
    print(f"  [INFO] Total articles to save: {len(all_articles)}")

    # Save to data directory
    import os
    os.makedirs('data', exist_ok=True)
    import os
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    data_dir = os.path.join(project_root, 'data')
    os.makedirs(data_dir, exist_ok=True)
    json_filename = os.path.join(data_dir, 'lankadeepa_latest_news.json')

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
        print(f"\n[INFO] Scraping complete!")
        print(f"[INFO] Saved {len(all_articles)} articles to {json_filename}")
        print(f"[INFO] Date range: {start_date} to {end_date}")

        # Count articles with images
        articles_with_images = sum(1 for article in all_articles if article['image_url'] and article['image_url'] != '')
        print(f"[IMAGE] Articles with images: {articles_with_images}/{len(all_articles)}")


def main_incremental() -> int:
    """Per-section checkpoints — each category URL tracks its own newest article."""
    json_filename = data_json_path("lankadeepa_latest_news.json")
    section_keys = [c[0] for c in LANKADEEPA_CATEGORIES]
    bootstrap = not any(get_section_checkpoint(json_filename, k)[0] for k in section_keys)
    max_articles = incremental_fetch_limit(bootstrap=bootstrap)

    known_previous = load_known_links(json_filename)
    if known_previous:
        print(f"[INCREMENTAL] Skipping {len(known_previous)} URL(s) from previous file")

    print("[INCREMENTAL] Lankadeepa — per-section checkpoints")
    if bootstrap:
        print(f"[INCREMENTAL] No checkpoint; bootstrap max {max_articles} articles")
    else:
        print(f"[INCREMENTAL] Run safety cap: {max_articles} new articles")

    driver = create_standard_driver(use_undetected=True)
    driver.set_page_load_timeout(60)

    section_links: dict[str, list[str]] = {}
    for name, url in LANKADEEPA_CATEGORIES:
        print(f"\n[PHASE 1] {name}: {url}")
        try:
            links = collect_lankadeepa_links(driver, url)
            section_links[name] = links
            print(f"  {len(links)} links")
        except Exception as e:
            print(f"  [ERROR] {e}")
            section_links[name] = []

    new_articles: list[dict] = []
    seen_this_run: set[str] = set()
    cap_hit = False

    for name, _url in LANKADEEPA_CATEGORIES:
        if cap_hit:
            break
        links = section_links.get(name, [])
        sec_ckpt, _ = get_section_checkpoint(json_filename, name)
        print(f"\n[PHASE 2] {name} — checkpoint: {(sec_ckpt or 'None')[:70]}")

        for i, link in enumerate(links, 1):
            norm = normalize_link(link)
            if sec_ckpt and normalize_link(sec_ckpt) == norm:
                print("  [STOP] Reached section checkpoint")
                break
            if norm in known_previous:
                print(f"  [STOP] Already in previous run: {link[:70]}")
                break
            if norm in seen_this_run:
                continue

            print(f"\n  [INFO] New {i}: {link[:80]}...")
            try:
                driver.get(link)
                time.sleep(2)
                meta = extract_with_timeout(driver, timeout_seconds=30)
                row = article_from_content(meta, link) if meta else None
                if not row or (
                    not row.get("title")
                    and not row.get("description")
                    and not row.get("summary")
                ):
                    print(f"  [SKIP] Empty row: {link[:80]}")
                    continue
                new_articles.append(row)
                seen_this_run.add(norm)
                print(f"  [+] {(row.get('title') or '')[:70]}")
            except Exception as e:
                print(f"  [ERROR] {e}")

            if reached_incremental_limit(len(new_articles), bootstrap=bootstrap):
                label = "Bootstrap" if bootstrap else "Run safety"
                print(f"[INCREMENTAL] {label} limit ({max_articles}) reached.")
                cap_hit = True
                break

            time.sleep(0.5)

        if links:
            head_url = links[0]
            head_title = ""
            head_norm = normalize_link(head_url)
            for a in new_articles:
                if normalize_link(a.get("link", "")) == head_norm:
                    head_title = a.get("title", "") or ""
                    break
            update_section_checkpoints(json_filename, {name: (head_url, head_title)})
            print(f"  [CKPT] {name} newest on page: {head_url[:70]}")

    for name, links in section_links.items():
        sec_ckpt, _ = get_section_checkpoint(json_filename, name)
        if not sec_ckpt and links:
            update_section_checkpoints(json_filename, {name: (links[0], "")})
            print(f"[SEED] {name}: {links[0][:80]}")

    try:
        driver.quit()
    except Exception:
        pass

    print(f"\n[INCREMENTAL] New articles: {len(new_articles)}")
    if new_articles:
        save_replace_only(json_filename, new_articles)
    else:
        print("[INCREMENTAL] No new articles — keeping existing data file unchanged")
    return len(new_articles)


if __name__ == "__main__":
    _scraper_dir = os.path.dirname(os.path.abspath(__file__))
    if _scraper_dir not in sys.path:
        sys.path.insert(0, _scraper_dir)

    if is_incremental_mode():
        main_incremental()
        sys.exit(0)

    # Check if date range is provided as command line arguments
    if len(sys.argv) >= 3:
        try:
            start_date = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
            end_date = datetime.strptime(sys.argv[2], "%Y-%m-%d").date()
            
            # Check if third argument specifies page type
            page_type = sys.argv[3] if len(sys.argv) >= 4 else "all"
            
            if page_type == "latest":
                print(f"[INFO] Scraping LATEST NEWS only")
                main(start_date, end_date, page_type="latest")
            elif page_type == "features":
                print(f"[INFO] Scraping FEATURES only")
                main(start_date, end_date, page_type="features")
            elif page_type == "politics":
                print(f"[INFO] Scraping POLITICS only")
                main(start_date, end_date, page_type="politics")
            else:
                print(f"[INFO] Scraping ALL pages (latest + features + politics)")
                main(start_date, end_date, page_type="all")
        except ValueError as e:
            print(f"[ERROR] Invalid date format. Use YYYY-MM-DD. Error: {e}")
            print("[INFO] Example: python lankadeepa_selenium_json.py 2026-01-28 2026-01-29 [latest|features|politics|all]")
    else:
        main()