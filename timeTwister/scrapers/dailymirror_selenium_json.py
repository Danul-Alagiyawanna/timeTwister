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

BASE_URL = "https://www.dailymirror.lk/latest-news/108"

# List-page metadata keyed by normalized URL (used when article page extract fails on CI)
_LIST_PAGE_HINTS: dict[str, dict] = {}


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
    """Extract detailed content from article page using XPath."""
    start_time = time.time()
    print(f"     [EXTRACT] Function started")
    try:
        print(f"     [EXTRACT] Current URL: {driver.current_url}")
        print(f"     [EXTRACT] Page title: {driver.title}")
        
        # Check elapsed time
        if time.time() - start_time > max_elapsed_time:
            raise TimeoutError("Extraction timeout exceeded")
        
        # Wait for page to load (reduced from 3 to 2 seconds)
        print(f"     [EXTRACT] Waiting 2 seconds for initial page load...")
        time.sleep(2)

        # Check elapsed time
        if time.time() - start_time > max_elapsed_time:
            raise TimeoutError("Extraction timeout exceeded")

        # Wait for the article content div to load - try multiple selectors (reduced timeouts)
        content_loaded = False
        print(f"     [EXTRACT] Waiting for article content div (XPath)...")
        try:
            WebDriverWait(driver, 8).until(  # Reduced from 10 to 8
                EC.presence_of_element_located((By.XPATH, "/html/body/div[9]/div/div/div/div[1]/div[2]/div"))
            )
            content_loaded = True
            print(f"     [EXTRACT] ✓ Article content div found via XPath")
        except Exception as e:
            print(f"     [EXTRACT] XPath wait failed: {e}")
            # Check elapsed time
            if time.time() - start_time > max_elapsed_time:
                raise TimeoutError("Extraction timeout exceeded")
            # Try CSS selector as fallback
            print(f"     [EXTRACT] Trying CSS selector fallback...")
            try:
                WebDriverWait(driver, 5).until(  # Keep at 5
                    EC.presence_of_element_located((By.CSS_SELECTOR, "div.a-content.mmfpmf"))
                )
                content_loaded = True
                print(f"     [EXTRACT] ✓ Article content div found via CSS selector")
            except Exception as e2:
                print(f"     [EXTRACT] CSS selector wait also failed: {e2}")
        
        if not content_loaded:
            print(f"     [EXTRACT] ⚠ Warning: Could not confirm article content div loaded, proceeding anyway...")

        soup = BeautifulSoup(driver.page_source, 'html.parser')

        # Extract publication date from meta tag
        date_published = None
        date_meta = soup.find('meta', attrs={'itemprop': 'datePublished'})
        if date_meta and date_meta.has_attr('content'):
            try:
                # Parse the exact timestamp: "2025-07-19 19:59:29"
                date_published = datetime.strptime(date_meta['content'], "%Y-%m-%d %H:%M:%S")
                print(f"     Found exact date: {date_meta['content']}")
            except ValueError as e:
                print(f"     Error parsing date '{date_meta['content']}': {e}")
        else:
            print(f"     No datePublished meta tag found")

        # Extract image URL from OpenGraph meta tag
        image_url = ""
        og_image = soup.find('meta', attrs={'property': 'og:image'})
        if og_image and og_image.has_attr('content'):
            image_url = og_image['content']
            print(f"     Found image: {image_url[:60]}...")

        # Extract full article text from paragraph tags within the specified div
        full_article_text = ""
        extraction_successful = False
        
        # Debug: Check page structure
        print(f"     [DEBUG] Current URL: {driver.current_url}")
        print(f"     [DEBUG] Page title: {driver.title}")
        
        # Debug: Try to find divs with class a-content
        try:
            all_content_divs = driver.find_elements(By.CSS_SELECTOR, "div.a-content")
            print(f"     [DEBUG] Found {len(all_content_divs)} div(s) with class 'a-content'")
            for idx, div in enumerate(all_content_divs):
                classes = div.get_attribute('class')
                print(f"     [DEBUG]   Div {idx+1} classes: {classes}")
        except Exception as e:
            print(f"     [DEBUG] Error finding a-content divs: {e}")
        
        # Debug: Check if the XPath element exists
        try:
            test_div = driver.find_element(By.XPATH, "/html/body/div[9]/div/div/div/div[1]/div[2]/div")
            print(f"     [DEBUG] XPath element found, tag: {test_div.tag_name}, classes: {test_div.get_attribute('class')}")
        except Exception as e:
            print(f"     [DEBUG] XPath element not found: {e}")
        
        # Check elapsed time before starting extraction strategies
        if time.time() - start_time > max_elapsed_time:
            raise TimeoutError("Extraction timeout exceeded")
        
        # Strategy 1: Try XPath as specified by user
        if not extraction_successful:
            try:
                # Check elapsed time
                if time.time() - start_time > max_elapsed_time:
                    raise TimeoutError("Extraction timeout exceeded")
                
                # Wait for the div to be present and visible (reduced timeout)
                article_div = WebDriverWait(driver, 8).until(
                    EC.presence_of_element_located((By.XPATH, "/html/body/div[9]/div/div/div/div[1]/div[2]/div"))
                )
                
                # Check elapsed time
                if time.time() - start_time > max_elapsed_time:
                    raise TimeoutError("Extraction timeout exceeded")
                
                # Wait a bit more for dynamic content to load (reduced)
                time.sleep(1)
                
                # Find all paragraph tags within this div
                paragraphs = article_div.find_elements(By.TAG_NAME, "p")
                print(f"     [DEBUG] Found {len(paragraphs)} <p> tags in XPath div")
                
                # Also try finding paragraphs using XPath directly
                if len(paragraphs) == 0:
                    print(f"     [DEBUG] No paragraphs found with TAG_NAME, trying XPath...")
                    try:
                        paragraph_elements = driver.find_elements(By.XPATH, "/html/body/div[9]/div/div/div/div[1]/div[2]/div//p")
                        paragraphs = paragraph_elements
                        print(f"     [DEBUG] Found {len(paragraphs)} <p> tags using XPath //p")
                    except Exception as e:
                        print(f"     [DEBUG] XPath //p also failed: {e}")
                
                paragraph_texts = []
                for idx, p in enumerate(paragraphs):
                    try:
                        # Try to get text - wait for element to be visible if needed
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
                    print(f"     ✓ Extracted {len(paragraph_texts)} paragraphs using XPath ({len(full_article_text)} chars)")
                else:
                    print(f"     [DEBUG] No paragraph text found in XPath div (found {len(paragraphs)} <p> tags but all empty)")
                    # Try getting innerHTML to see what's in the div
                    try:
                        inner_html = article_div.get_attribute('innerHTML')
                        print(f"     [DEBUG] Div innerHTML length: {len(inner_html)} chars")
                        if len(inner_html) < 500:
                            print(f"     [DEBUG] Div content preview: {inner_html[:200]}...")
                    except:
                        pass
            except Exception as e:
                print(f"     XPath extraction failed: {e}")
                import traceback
                traceback.print_exc()
        
        # Strategy 2: Try using class name from dev tools (a-content mmfpmf)
        if not extraction_successful:
            try:
                article_div = driver.find_element(By.CSS_SELECTOR, "div.a-content.mmfpmf")
                paragraphs = article_div.find_elements(By.TAG_NAME, "p")
                paragraph_texts = []
                for p in paragraphs:
                    text = p.text.strip()
                    if text:
                        paragraph_texts.append(text)
                if paragraph_texts:
                    full_article_text = "\n\n".join(paragraph_texts)
                    extraction_successful = True
                    print(f"     Extracted {len(paragraph_texts)} paragraphs using CSS selector ({len(full_article_text)} chars)")
            except Exception as e:
                print(f"     CSS selector extraction failed: {e}")
        
        # Strategy 3: Try BeautifulSoup with XPath (nth-of-type selector)
        if not extraction_successful:
            try:
                # Try multiple BeautifulSoup selectors
                article_div_soup = None
                
                # Try nth-of-type selector
                try:
                    article_div_soup = soup.select_one('body > div:nth-of-type(9) > div > div > div > div:nth-of-type(1) > div:nth-of-type(2) > div')
                except:
                    pass
                
                # If that fails, try finding by structure
                if not article_div_soup:
                    try:
                        body = soup.find('body')
                        if body:
                            divs = body.find_all('div', recursive=False)
                            if len(divs) >= 9:
                                target_div = divs[8]  # 9th div (0-indexed)
                                # Navigate: div > div > div > div[0] > div[1] > div
                                nested = target_div
                                for _ in range(3):  # Go down 3 levels
                                    nested_divs = nested.find_all('div', recursive=False)
                                    if nested_divs:
                                        nested = nested_divs[0]
                                    else:
                                        break
                                # Now get div[1] > div
                                if nested:
                                    nested_divs = nested.find_all('div', recursive=False)
                                    if len(nested_divs) >= 2:
                                        article_div_soup = nested_divs[1].find('div', recursive=False)
                    except Exception as e:
                        print(f"     [DEBUG] BeautifulSoup structure navigation failed: {e}")
                
                if article_div_soup:
                    paragraphs = article_div_soup.find_all('p')
                    paragraph_texts = [p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)]
                    if paragraph_texts:
                        full_article_text = "\n\n".join(paragraph_texts)
                        extraction_successful = True
                        print(f"     Extracted {len(paragraph_texts)} paragraphs using BeautifulSoup XPath fallback")
                else:
                    print(f"     [DEBUG] BeautifulSoup could not find the target div")
            except Exception as e:
                print(f"     BeautifulSoup XPath fallback failed: {e}")
                import traceback
                traceback.print_exc()
        
        # Strategy 4: Try BeautifulSoup with class selector
        if not extraction_successful:
            try:
                article_div_soup = soup.select_one('div.a-content.mmfpmf')
                if article_div_soup:
                    paragraphs = article_div_soup.find_all('p')
                    paragraph_texts = [p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)]
                    if paragraph_texts:
                        full_article_text = "\n\n".join(paragraph_texts)
                        extraction_successful = True
                        print(f"     Extracted {len(paragraph_texts)} paragraphs using BeautifulSoup class selector")
            except Exception as e:
                print(f"     BeautifulSoup class selector fallback failed: {e}")
        
        # Strategy 5: Fallback - get all text from the div if paragraphs failed
        if not extraction_successful:
            try:
                article_div = driver.find_element(By.XPATH, "/html/body/div[9]/div/div/div/div[1]/div[2]/div")
                # Get all text content from the div
                all_text = article_div.text.strip()
                if all_text and len(all_text) > 50:  # Only use if substantial text
                    full_article_text = all_text
                    extraction_successful = True
                    print(f"     Extracted full text from div ({len(full_article_text)} chars) - no paragraph tags found")
            except Exception as e:
                print(f"     Fallback text extraction also failed: {e}")
        
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
            'description': full_article_text,  # Use full article text instead of short description
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
        
        time.sleep(5)  # Extra wait for Cloudflare and dynamic content
        print(f"[DEBUG] Current page title: {driver.title}")
        print(f"[DEBUG] Current URL: {driver.current_url}")
        print(f"[DEBUG] Page source length: {len(driver.page_source)} characters")
    except Exception as e:
        print(f"[ERROR] Failed to navigate to page: {e}")
        import traceback
        traceback.print_exc()
        return [], 0, 0

    try:
        print(f"[DEBUG] Waiting for article container element...")
        # Wait for the main article container to load
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.XPATH, "/html/body/div[7]/div/div/div/div[1]/div[2]"))
        )
        print(f"   List page loaded successfully")
    except Exception as e:
        print(f"   Timeout waiting for articles: {e}")
        print(f"[DEBUG] Page source length: {len(driver.page_source)}")
        print(f"[DEBUG] Checking if Cloudflare challenge is present...")
        page_source_lower = driver.page_source.lower()
        if 'cloudflare' in page_source_lower or 'challenge' in page_source_lower or 'checking your browser' in page_source_lower:
            print(f"[WARNING] Cloudflare challenge detected! Waiting longer...")
            time.sleep(10)  # Wait longer for Cloudflare to pass
            try:
                WebDriverWait(driver, 30).until(
                    EC.presence_of_element_located((By.XPATH, "/html/body/div[7]/div/div/div/div[1]/div[2]"))
                )
                print(f"   List page loaded after Cloudflare challenge")
            except:
                print(f"[ERROR] Still couldn't load after Cloudflare wait")
                return [], 0, 0
        else:
            return [], 0, 0

    articles_found = []
    articles_in_range = 0
    articles_outside_range = 0
    consecutive_outside_range = 0
    max_consecutive_outside = 2

    # Find all article divs using XPath pattern
    # Articles are in /html/body/div[7]/div/div/div/div[1]/div[2]/div[N] where N is 1, 2, 3, etc.
    max_articles = 30  # Reasonable limit per page

    print(f"   Scanning for articles using XPath patterns...")

    for i in range(1, max_articles + 1):
        try:
            # Build XPaths for this article index
            base_xpath = f"/html/body/div[7]/div/div/div/div[1]/div[2]/div[{i}]"
            heading_xpath = f"{base_xpath}/div/div[1]/a[2]/h3"
            date_xpath = f"{base_xpath}/div/div[1]/div/div[1]/h4"
            description_xpath = f"{base_xpath}/div/div[1]/a[3]/p"
            image_xpath = f"{base_xpath}/div/div[2]/a/img"

            # Try to find the heading element (if it doesn't exist, we've reached the end)
            try:
                heading_element = driver.find_element(By.XPATH, heading_xpath)
            except:
                print(f"   No more articles found (checked up to article {i-1})")
                break

            # Extract data from list page using XPath
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
                description_element = driver.find_element(By.XPATH, description_xpath)
                description = description_element.text.strip()
            except:
                description = ""

            try:
                image_element = driver.find_element(By.XPATH, image_xpath)
                image_url = image_element.get_attribute('src')
            except:
                image_url = ""

            # Get article link
            try:
                link_element = heading_element.find_element(By.XPATH, "..")  # Parent <a> tag
                article_link = link_element.get_attribute('href')
            except:
                print(f"   Article {i}: Could not find link, skipping")
                continue

            print(f"\n   Article {i}: {title[:60]}...")
            print(f"     Link: {article_link}")
            print(f"     Date text: {date_text}")

            # Parse the date from the date text
            article_date = None
            if date_text:
                try:
                    # Try to parse various date formats
                    date_text_clean = date_text.strip().lower()

                    # Handle relative times (e.g., "11 minute ago", "2 hours ago", "3 days ago")
                    if 'ago' in date_text_clean:
                        now = datetime.now()

                        # Extract number from text
                        match = re.search(r'(\d+)\s*(minute|hour|day|week|month)', date_text_clean)
                        if match:
                            value = int(match.group(1))
                            unit = match.group(2)

                            if 'minute' in unit:
                                article_date = now - timedelta(minutes=value)
                            elif 'hour' in unit:
                                article_date = now - timedelta(hours=value)
                            elif 'day' in unit:
                                article_date = now - timedelta(days=value)
                            elif 'week' in unit:
                                article_date = now - timedelta(weeks=value)
                            elif 'month' in unit:
                                article_date = now - timedelta(days=value*30)  # Approximate

                            if article_date:
                                print(f"     Parsed relative date: {article_date.strftime('%Y-%m-%d %H:%M:%S')} from '{date_text}'")

                    # Try absolute date formats if not a relative time
                    if not article_date:
                        # Try format: "23 Nov 2025"
                        try:
                            article_date = datetime.strptime(date_text.strip(), "%d %b %Y")
                        except:
                            pass

                        # Try format: "November 23, 2025"
                        if not article_date:
                            try:
                                article_date = datetime.strptime(date_text.strip(), "%B %d, %Y")
                            except:
                                pass

                        # Try other common formats
                        if not article_date:
                            for fmt in ["%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y", "%d %B %Y"]:
                                try:
                                    article_date = datetime.strptime(date_text.strip(), fmt)
                                    break
                                except:
                                    continue

                        if article_date:
                            print(f"     Parsed date: {article_date.strftime('%Y-%m-%d')}")

                except Exception as e:
                    print(f"     Could not parse date '{date_text}': {e}")

            # Check if article is in date range
            if article_date is None:
                print(f"     No date found, skipping article")
                consecutive_outside_range += 1
            elif is_article_in_date_range(article_date, start_date, end_date):
                print(f"     Article is in date range! Attempting to extract full content...")

                # Store the current window handle (list page) and list page data as fallback
                original_window = driver.current_window_handle
                opened_new_tab = False
                
                # Initialize with list page data as fallback
                final_date = article_date
                final_title = title
                final_image = image_url
                final_description = description  # Use list page description as default
                extraction_successful = False
                
                try:
                    # Try to open article in a new tab
                    print(f"     [NEW TAB] Attempting to open article in new tab: {article_link}")
                    
                    # Store current window handles
                    windows_before = set(driver.window_handles)
                    
                    # Open article link in a new tab using JavaScript (safer method with arguments)
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
                            print(f"     [FALLBACK] Will use list page description")
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
                            driver.set_page_load_timeout(30)  # Reduced timeout
                            driver.get(article_link)
                            opened_new_tab = False
                            print(f"     [NAVIGATE] Direct navigation complete, waiting for page to load...")
                            time.sleep(3)
                            
                            # Verify navigation was successful
                            current_url = driver.current_url
                            if current_url != article_link and article_link not in current_url:
                                print(f"     [WARNING] URL mismatch - expected: {article_link}, got: {current_url}")
                                print(f"     [FALLBACK] Will use list page description")
                                raise Exception("URL mismatch after navigation")
                            
                            print(f"     [NAVIGATE] Current URL: {current_url}")
                            print(f"     [NAVIGATE] Page title: {driver.title[:80]}...")
                        except Exception as nav_error:
                            print(f"     [NAVIGATE] ✗ Navigation timeout/error: {nav_error}")
                            print(f"     [FALLBACK] Will use list page description")
                            raise  # Re-raise to trigger fallback handling

                    # Extract detailed content from the article page with 30 second timeout
                    print(f"     [EXTRACT] Starting content extraction (30s timeout)...")
                    try:
                        article_content = extract_with_timeout(driver, timeout_seconds=30)
                        
                        if article_content is None:
                            # Timeout occurred
                            print(f"     [TIMEOUT] Extraction exceeded 30 seconds, using list page description")
                            # Keep using list page data (already set above)
                        else:
                            print(f"     [EXTRACT] Content extraction completed")
                            print(f"     [EXTRACT] Got article_content keys: {list(article_content.keys())}")
                            print(f"     [EXTRACT] Description length: {len(article_content.get('description', ''))}")

                            # Use more accurate date from article page if available
                            final_date = article_content['date_published'] if article_content.get('date_published') else article_date
                            final_title = article_content['title'] if article_content.get('title') else title
                            final_image = article_content['image_url'] if article_content.get('image_url') else image_url
                                    
                            # Prioritize extracted full article text - only fall back to list page description if extraction completely failed
                            extracted_desc = article_content.get('description', '').strip()
                            if extracted_desc and len(extracted_desc) > 0:
                                final_description = extracted_desc
                                extraction_successful = True
                                print(f"     ✓ Using extracted full article text ({len(final_description)} chars)")
                            else:
                                print(f"     ⚠ Extracted description is empty, using list page description as fallback")
                                final_description = description  # Use list page description
                    except Exception as extract_error:
                        print(f"     [EXTRACT] ✗ Error during extraction: {extract_error}")
                        print(f"     [FALLBACK] Will use list page description")
                        # Keep using list page data (already set above)
                    
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
                                # Continue anyway - might still work

                except Exception as e:
                    print(f"     ✗ Error during navigation/extraction: {e}")
                    print(f"     [FALLBACK] Using list page data (title, description, image)")
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
                    print(f"     ✓ Article saved with list page description (fallback)")
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


def _prepare_list_page(driver, list_url: str) -> bool:
    """Navigate to a list page and wait for Cloudflare / article container (shared with incremental)."""
    try:
        driver.get(list_url)
        WebDriverWait(driver, 30).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
        time.sleep(5)
    except Exception as e:
        print(f"[ERROR] Failed to navigate to list page: {e}")
        return False

    try:
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.XPATH, "/html/body/div[7]/div/div/div/div[1]/div[2]"))
        )
        return True
    except Exception:
        page_source_lower = driver.page_source.lower()
        if (
            "cloudflare" in page_source_lower
            or "challenge" in page_source_lower
            or "checking your browser" in page_source_lower
        ):
            print("[WARNING] Cloudflare challenge — waiting longer...")
            time.sleep(10)
            try:
                WebDriverWait(driver, 30).until(
                    EC.presence_of_element_located(
                        (By.XPATH, "/html/body/div[7]/div/div/div/div[1]/div[2]")
                    )
                )
                return True
            except Exception:
                return False
        return False


def _normalize_article_link(url: str) -> str:
    from incremental import normalize_link

    return normalize_link(url)


def collect_list_page_links(driver, list_url: str) -> list[str]:
    """Ordered article URLs from one list page (same XPath as date-range main)."""
    global _LIST_PAGE_HINTS
    _LIST_PAGE_HINTS = {}
    if not _prepare_list_page(driver, list_url):
        return []
    links: list[str] = []
    for i in range(1, 31):
        base_xpath = f"/html/body/div[7]/div/div/div/div[1]/div[2]/div[{i}]"
        heading_xpath = f"{base_xpath}/div/div[1]/a[2]/h3"
        date_xpath = f"{base_xpath}/div/div[1]/div/div[1]/h4"
        description_xpath = f"{base_xpath}/div/div[1]/a[3]/p"
        image_xpath = f"{base_xpath}/div/div[2]/a/img"
        try:
            heading = driver.find_element(By.XPATH, heading_xpath)
            parent = heading.find_element(By.XPATH, "..")
            href = parent.get_attribute("href")
            if not href:
                continue
            title = (heading.text or "").strip()
            try:
                date_text = driver.find_element(By.XPATH, date_xpath).text.strip()
            except Exception:
                date_text = ""
            try:
                description = driver.find_element(By.XPATH, description_xpath).text.strip()
            except Exception:
                description = ""
            try:
                image_url = driver.find_element(By.XPATH, image_xpath).get_attribute("src") or ""
            except Exception:
                image_url = ""
            links.append(href)
            _LIST_PAGE_HINTS[_normalize_article_link(href)] = {
                "title": title,
                "list_description": description,
                "list_image": image_url,
                "date_text": date_text,
            }
        except Exception:
            break
    print(f"[INFO] collect_list_page_links: {len(links)} URLs from {list_url}")
    return links


def create_driver(headless=None):
    """Same Chrome setup as main(); headless on CI (GHA has no display)."""
    import os

    if headless is None:
        headless = os.getenv("CI", "").lower() in ("1", "true", "yes")

    if USE_UNDETECTED:
        print("[INFO] Using undetected-chromedriver to bypass Cloudflare...")
        options = uc.ChromeOptions()
        options.page_load_strategy = "eager"
        if headless:
            options.add_argument("--headless=new")
        prefs = {"profile.default_content_setting_values": {"popups": 1}}
        options.add_experimental_option("prefs", prefs)
        try:
            driver = uc.Chrome(options=options, use_subprocess=True)
        except Exception as e:
            error_msg = str(e)
            match = re.search(r"Current browser version is (\d+)", error_msg)
            if not match:
                match = re.search(r"only supports Chrome version (\d+)", error_msg)
            if match:
                major_version = int(match.group(1))
                options_retry = uc.ChromeOptions()
                options_retry.page_load_strategy = "eager"
                if headless:
                    options_retry.add_argument("--headless=new")
                options_retry.add_experimental_option(
                    "prefs", {"profile.default_content_setting_values": {"popups": 1}}
                )
                driver = uc.Chrome(
                    options=options_retry,
                    use_subprocess=True,
                    version_main=major_version,
                )
            else:
                raise
    else:
        print("[WARNING] Falling back to regular Selenium (may be blocked by Cloudflare)...")
        chrome_options = Options()
        chrome_options.page_load_strategy = "eager"
        if headless:
            chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option("useAutomationExtension", False)
        chrome_options.add_argument(
            "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        prefs = {"profile.default_content_setting_values": {"popups": 1}}
        chrome_options.add_experimental_option("prefs", prefs)
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=chrome_options,
        )
        driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

    driver.set_page_load_timeout(60)
    return driver


def _parse_list_date_text(date_text: str) -> datetime | None:
    if not date_text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(date_text.strip(), fmt)
        except ValueError:
            continue
    return None


def article_row_from_extract(
    meta: dict | None, link: str, hint: dict | None = None
) -> dict | None:
    """JSON row matching date-range main() output (title, link, summary, date, image_url, date_source)."""
    hint = hint or {}
    body = ""
    title = ""
    image_url = ""
    article_date = None
    from_article_page = False

    if meta:
        body = (meta.get("description") or "").strip()
        title = (meta.get("title") or "").strip()
        image_url = (meta.get("image_url") or "").strip()
        article_date = meta.get("date_published")
        from_article_page = bool(body)

    if not title:
        title = (hint.get("title") or "").strip()
    if not body:
        body = (hint.get("list_description") or "").strip()
    if not image_url:
        image_url = (hint.get("list_image") or "").strip()

    if not title and not body:
        print(f"[SKIP] No title or body for {link[:80]}...")
        return None

    if not article_date:
        article_date = _parse_list_date_text(hint.get("date_text") or "")

    standardized_date = (
        article_date.strftime("%Y-%m-%d %H:%M:%S")
        if article_date
        else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )

    if from_article_page:
        date_source = "Article page"
    elif body or title:
        date_source = "List page fallback"
    else:
        date_source = "Incremental scrape"

    return {
        "title": title,
        "link": (meta or {}).get("link") or link,
        "summary": body,
        "date": standardized_date,
        "image_url": image_url,
        "date_source": date_source,
    }


def fetch_article_incremental(driver, link: str) -> dict | None:
    import os

    hint = _LIST_PAGE_HINTS.get(_normalize_article_link(link), {})
    for attempt in range(2):
        try:
            driver.get(link)
            time.sleep(5 if os.getenv("CI") else 2)
            meta = extract_with_timeout(driver, timeout_seconds=60)
            row = article_row_from_extract(meta, link, hint)
            if row and (row.get("summary") or "").strip():
                return row
            if row and attempt == 1:
                return row
        except Exception as e:
            print(f"[ERROR] fetch attempt {attempt + 1}: {e}")
        time.sleep(2)
    return article_row_from_extract(None, link, hint)


def main_incremental():
    """Incremental on GHA: same driver + extract_article_content + archive merge as local main()."""
    import os

    scraper_dir = os.path.dirname(os.path.abspath(__file__))
    if scraper_dir not in sys.path:
        sys.path.insert(0, scraper_dir)
    from incremental_runner import run_incremental_scraper

    pages = [
        ("latest", BASE_URL),
        ("latest-p30", f"{BASE_URL}/30"),
    ]

    run_incremental_scraper(
        outlet_name="Daily Mirror",
        data_filename="dailymirror_latest_news.json",
        pages=pages,
        collect_links=collect_list_page_links,
        fetch_article=fetch_article_incremental,
        create_driver=create_driver,
        use_undetected=False,
        save_mode="merge",
        sleep_between_articles=1.0,
        sleep_after_list_page=2.0,
    )


def main(start_date=None, end_date=None):
    """Main function with click-and-back navigation approach."""
    
    # Default to yesterday and today if no dates provided
    if not start_date or not end_date:
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=1)
        print(f"[DATE] No date range provided, using default: {start_date} to {end_date}")
    else:
        print(f"[DATE] Scraping articles from {start_date} to {end_date}")
    
    print("[INFO] Starting Enhanced Daily Mirror scraper with click-and-back navigation...")

    driver = create_driver(headless=False)
    print("[INFO] Page load timeout set to 60 seconds")

    all_articles = []
    total_articles_in_range = 0
    total_articles_outside_range = 0
    max_pages = 10  # Process more pages since we can stop early
    consecutive_empty_pages = 0
    max_consecutive_empty = 3
    
    print(f"\n[INFO] Processing articles page by page with click-and-back navigation...")
    
    for page_num in range(max_pages):
        if page_num == 0:
            list_url = BASE_URL
        else:
            list_url = f"{BASE_URL}/{page_num * 30}"
        
        print(f"\n[INFO] Page {page_num + 1}:")
        
        try:
            articles, page_in_range, page_outside_range = process_articles_from_list_page(
                driver, list_url, start_date, end_date
            )
            
            all_articles.extend(articles)
            total_articles_in_range += page_in_range
            total_articles_outside_range += page_outside_range
            
            # Check if we should continue to next page
            if page_in_range == 0:
                consecutive_empty_pages += 1
                print(f"  [WARNING] No articles in range on this page ({consecutive_empty_pages}/{max_consecutive_empty} consecutive)")
                if consecutive_empty_pages >= max_consecutive_empty:
                    print(f"  [ERROR] Stopping: {max_consecutive_empty} consecutive pages with no articles in date range")
                    break
            else:
                consecutive_empty_pages = 0  # Reset counter
            
            # Brief delay between pages
            time.sleep(2)
            
        except Exception as e:
            print(f"  [ERROR] Error processing page {page_num + 1}: {e}")
            continue
    
    # Properly close the driver
    # Note: You may see a harmless "OSError: [WinError 6] The handle is invalid" 
    # error from undetected-chromedriver's destructor during garbage collection.
    # This is a known library issue and can be safely ignored - the browser is already properly closed.
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
    json_filename = os.path.join(data_dir, 'dailymirror_latest_news.json')

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
        main_incremental()
        sys.exit(0)

    # Check if date range is provided as command line arguments
    if len(sys.argv) >= 3:
        try:
            start_date = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
            end_date = datetime.strptime(sys.argv[2], "%Y-%m-%d").date()
            main(start_date, end_date)
        except ValueError as e:
            print(f"[ERROR] Invalid date format. Use YYYY-MM-DD. Error: {e}")
            print("[INFO] Example: python dailymirror_selenium_json.py 2025-01-18 2025-01-19")
    else:
        main()  # Use default date range 