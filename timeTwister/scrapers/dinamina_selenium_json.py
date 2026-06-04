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
    get_section_checkpoint,
    incremental_fetch_limit,
    is_incremental_mode,
    load_known_links,
    normalize_link,
    reached_incremental_limit,
    save_replace_only,
    update_section_checkpoints,
)
from incremental_runner import article_from_content, data_json_path

DINAMINA_CATEGORIES = [
    ("local", "https://www.dinamina.lk/category/local/"),
    ("politics", "https://www.dinamina.lk/category/politics/"),
    ("editorial", "https://www.dinamina.lk/category/editorial/"),
    ("sports", "https://www.dinamina.lk/category/sports/"),
    ("features", "https://www.dinamina.lk/category/features/"),
    ("business", "https://www.dinamina.lk/category/business/"),
    ("world", "https://www.dinamina.lk/category/world/"),
]

# Reconfigure stdout/stderr to use UTF-8 to prevent UnicodeEncodeError on Windows
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except AttributeError:
    pass

BASE_URL = "https://www.dinamina.lk/category/local/"


_ARTICLE_PATH_RE = re.compile(r"/20\d{2}/\d{1,2}/\d{1,2}/")


def _chrome_major_version() -> int | None:
    """Installed Chrome major version (GHA + local)."""
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


def _heading_xpath_for_index(article_idx: int) -> str:
    """Same layout as process_articles_from_page."""
    if article_idx == 0:
        return "/html/body/div[1]/div[3]/div[1]/div/ul/section/article/div[2]/div[1]/h2/a"
    if article_idx <= 4:
        li_idx = article_idx
        return f"/html/body/div[1]/div[3]/div[1]/div/ul/li[{li_idx}]/article/div[2]/h2/a"
    li_idx = article_idx
    return f"/html/body/div[1]/div[3]/div[1]/div/ul/li[{li_idx}]/article/div[2]/div/h2/a"


def _bs_fallback_category_links(driver) -> list[str]:
    """Fallback when XPath misses (e.g. plain headless HTML differs)."""
    soup = BeautifulSoup(driver.page_source, "html.parser")
    base = "https://www.dinamina.lk"
    links: list[str] = []
    seen: set[str] = set()
    for a in soup.select(
        "ul section article h2 a, ul li article h2 a, ul li article div h2 a, article h2 a"
    ):
        href = a.get("href") or ""
        if href.startswith("/"):
            href = base + href
        if href.startswith("http") and "dinamina" in href and href not in seen:
            if _ARTICLE_PATH_RE.search(href):
                seen.add(href)
                links.append(href)
    if links:
        return links
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("/"):
            href = base + href
        if (
            href.startswith("http")
            and "dinamina.lk" in href
            and "category" not in href
            and _ARTICLE_PATH_RE.search(href)
            and href not in seen
        ):
            seen.add(href)
            links.append(href)
    return links


def get_category_article_links(driver, max_links: int = 25) -> list[str]:
    """Ordered article URLs — XPath first (matches full scrape), then BS fallback."""
    WebDriverWait(driver, 30).until(
        lambda d: d.execute_script("return document.readyState") == "complete"
    )
    driver.execute_script("window.scrollTo(0, 0);")
    time.sleep(1)

    links: list[str] = []
    seen: set[str] = set()
    scroll_increment = 600
    consecutive_not_found = 0
    max_consecutive_not_found = 3

    for article_idx in range(max_links):
        if consecutive_not_found >= max_consecutive_not_found:
            break

        heading_xpath = _heading_xpath_for_index(article_idx)
        found = False
        for scroll_attempt in range(8):
            try:
                el = driver.find_element(By.XPATH, heading_xpath)
                href = (el.get_attribute("href") or "").strip()
                if href and href not in seen:
                    seen.add(href)
                    links.append(href)
                    found = True
                    consecutive_not_found = 0
                    break
            except Exception:
                if scroll_attempt < 7:
                    driver.execute_script(f"window.scrollBy(0, {scroll_increment});")
                    time.sleep(0.8)

        if not found:
            consecutive_not_found += 1

    if not links:
        links = _bs_fallback_category_links(driver)
        if not links:
            title = driver.title or ""
            print(
                f"  [WARN] 0 links — title={title[:60]!r} "
                f"html={len(driver.page_source)} bytes"
            )
    return links


def _create_dinamina_driver():
    """UC with version_main retry — same strategy as full main(), works on GHA."""
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
                err_msg = str(e)
                print(f"[WARNING] UC failed ({label}): {err_msg[:200]}")
                m = re.search(r"Current browser version is (\d+)", err_msg)
                if m:
                    v = int(m.group(1))
                    try:
                        driver = uc.Chrome(
                            options=options, use_subprocess=True, version_main=v
                        )
                        print(f"[INFO] UC started with version_main={v} from error")
                        driver.execute_script(
                            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
                        )
                        return driver
                    except Exception as e2:
                        last_err = e2

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
    chrome_options.add_experimental_option(
        "prefs", {"profile.default_content_setting_values": {"popups": 1}}
    )
    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()), options=chrome_options
    )
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return driver


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


def _link_to_section(link: str) -> str | None:
    """Map article URL path segment to DINAMINA_CATEGORIES key."""
    m2 = re.search(r"/20\d{2}/\d{1,2}/\d{1,2}/([^/]+)/", link, re.I)
    if not m2:
        return None
    slug = m2.group(1).lower()
    if slug == "featured":
        return "features"
    valid = {c[0] for c in DINAMINA_CATEGORIES}
    return slug if slug in valid else None


def _http_get(url: str, **kwargs):
    """curl_cffi first (GHA Cloudflare), then requests."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
        "Accept-Language": "en-US,en;q=0.9,si;q=0.8",
        "Referer": "https://www.dinamina.lk/",
    }
    import requests as req

    cf_import_error = None
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
        except ImportError as e:
            cf_import_error = e
            break
        except Exception as e:
            print(f"[WARN] curl_cffi ({profile}): {e}")

    if cf_import_error:
        print(f"[WARN] curl_cffi not installed: {cf_import_error}")

    try:
        r = req.get(url, timeout=20, allow_redirects=True, headers=headers, **kwargs)
        if r.status_code == 200 and len(r.text) > 200:
            return r
        print(f"[WARN] requests {r.status_code} for {url}")
    except Exception as e:
        print(f"[WARN] requests failed: {e}")
    return None


def _is_dinamina_article_url(link: str) -> bool:
    if not link or "dinamina.lk" not in link:
        return False
    if "epaper." in link or "archives." in link:
        return False
    return bool(_ARTICLE_PATH_RE.search(link))


def _decode_gnews_url(gnews_url: str) -> str:
    """Best-effort decode of Google News redirect URLs to dinamina.lk article URL."""
    import base64 as b64_mod

    m = re.search(r"/articles/([^?&#]+)", gnews_url)
    if not m:
        return gnews_url
    try:
        padded = m.group(1) + "=" * (-len(m.group(1)) % 4)
        raw = b64_mod.urlsafe_b64decode(padded)
        for pat in (
            rb"https?://(?:www\.)?dinamina\.lk/20\d{2}/\d{2}/\d{2}/[^\x00-\x20\"'<>]+",
            rb"https?://(?:www\.)?dinamina\.lk/20\d{2}/[^\x00-\x20\"'<>]+",
        ):
            fm = re.search(pat, raw)
            if fm:
                return fm.group(0).decode("utf-8", errors="ignore").rstrip(".")
        text = raw.decode("utf-8", errors="ignore")
        um = re.search(
            r"https?://(?:www\.)?dinamina\.lk/20\d{2}/\d{2}/\d{2}/[^\s\"'<>]+",
            text,
        )
        if um:
            return um.group(0).rstrip(".")
    except Exception:
        pass
    return gnews_url


def _html_to_plain(html: str) -> str:
    if not html:
        return ""
    try:
        text = BeautifulSoup(html, "html.parser").get_text(separator="\n", strip=True)
    except Exception:
        text = html.strip()
    return _strip_rss_boilerplate(text)


def _strip_rss_boilerplate(text: str) -> str:
    """Remove WordPress 'The post X appeared first on Y' footer from RSS bodies."""
    if not text:
        return ""
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


def _build_dinamina_article_row(
    *,
    title: str,
    link: str,
    date_str: str,
    body_text: str,
    image_url: str = "",
    author: str = "",
    rss_categories: list[str] | None = None,
    guid: str = "",
    date_source: str = "",
) -> dict:
    """Align RSS rows with full-scrape JSON (title, link, summary, description, date, image)."""
    section = _link_to_section(link) or ""
    row = {
        "title": title,
        "link": link,
        "summary": body_text,
        "description": body_text,
        "date": date_str,
        "image_url": image_url,
        "date_source": date_source or f"RSS: {date_str}",
    }
    if section:
        row["section"] = section
    if author:
        row["author"] = author
    if rss_categories:
        row["rss_categories"] = rss_categories
    if guid:
        row["guid"] = guid
    return row


def _parse_dinamina_rss(xml_text: str) -> list[dict]:
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime

    ns = {
        "content": "http://purl.org/rss/1.0/modules/content/",
        "dc": "http://purl.org/dc/elements/1.1/",
        "media": "http://search.yahoo.com/mrss/",
    }
    articles: list[dict] = []
    root = ET.fromstring(xml_text)
    # channel/item only — avoid nested comment feeds
    items = root.findall("./channel/item") or root.findall(".//item")
    for item in items:
        link = (item.findtext("link") or "").strip()
        if not link or "dinamina.lk" not in link:
            continue
        if not _is_dinamina_article_url(link):
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
        if not image_url and enclosure is not None and enclosure.get("type", "").startswith("image"):
            image_url = (enclosure.get("url") or "").strip()
        if not image_url:
            image_url = _first_img_from_html(content_html) or _first_img_from_html(desc_html)

        author_el = item.find("dc:creator", ns)
        author = (author_el.text or "").strip() if author_el is not None else ""
        rss_categories = [
            (c.text or "").strip() for c in item.findall("category") if (c.text or "").strip()
        ]
        guid = (item.findtext("guid") or "").strip()

        articles.append(
            _build_dinamina_article_row(
                title=title,
                link=link,
                date_str=date_str,
                body_text=body_text,
                image_url=image_url,
                author=author,
                rss_categories=rss_categories,
                guid=guid,
                date_source=f"RSS: {date_str}",
            )
        )
    return articles


def _fetch_dinamina_feed_articles() -> list[dict]:
    """Main site RSS (curl_cffi on GHA). Only real /YYYY/MM/DD/... article URLs."""
    for feed_url in (
        "https://www.dinamina.lk/feed/",
        "https://dinamina.lk/feed/",
    ):
        resp = _http_get(feed_url)
        if not resp:
            continue
        if "just a moment" in resp.text.lower()[:8000]:
            print(f"[WARN] Cloudflare interstitial on {feed_url}")
            continue
        print(f"[INFO] RSS fetched {feed_url} ({len(resp.text)} bytes)")
        try:
            items = _parse_dinamina_rss(resp.text)
            valid = [a for a in items if _is_dinamina_article_url(a.get("link", ""))]
            if valid:
                print(f"[INFO] RSS parsed {len(valid)} article URL(s)")
                return valid
            print(f"[WARN] RSS had {len(items)} items but 0 article URLs")
        except Exception as e:
            print(f"[WARN] RSS parse error: {e}")

    print("[WARN] Dinamina /feed/ blocked or empty — no Google News fallback (no article URLs there)")
    return []


def main_incremental_rss() -> int:
    """Per-section checkpoints via RSS (GHA — Cloudflare blocks Selenium)."""
    json_filename = data_json_path("dinamina_latest_news.json")
    section_keys = [c[0] for c in DINAMINA_CATEGORIES]
    bootstrap = not any(get_section_checkpoint(json_filename, k)[0] for k in section_keys)
    max_articles = incremental_fetch_limit(bootstrap=bootstrap)

    known_previous = load_known_links(json_filename)
    if known_previous:
        print(f"[INCREMENTAL] Skipping {len(known_previous)} URL(s) from previous file")

    print("[INCREMENTAL] Dinamina — RSS per-section (no Selenium)")
    if bootstrap:
        print(f"[INCREMENTAL] No checkpoint; bootstrap max {max_articles} articles")
    else:
        print(f"[INCREMENTAL] Run safety cap: {max_articles} new articles")

    all_feed = _fetch_dinamina_feed_articles()
    if not all_feed:
        print("[ERROR] No Dinamina article URLs from RSS — saving empty list")
        save_replace_only(json_filename, [])
        return 0

    section_links: dict[str, list[str]] = {k: [] for k in section_keys}
    for art in all_feed:
        sec = _link_to_section(art.get("link", ""))
        if sec and sec in section_links:
            section_links[sec].append(art["link"])

    for name in section_keys:
        print(f"[PHASE 1] {name}: {len(section_links[name])} links from feed")
    print(f"[PHASE 1] total in feed: {len(all_feed)} articles")

    new_articles: list[dict] = []
    seen_this_run: set[str] = set()
    section_stopped: set[str] = set()
    cap_hit = False

    print("\n[PHASE 2] RSS feed order (newest first, per-section stop)")
    for i, art in enumerate(all_feed, 1):
        if cap_hit:
            break
        link = art["link"]
        norm = normalize_link(link)
        sec = _link_to_section(link)
        if not sec:
            continue
        if sec in section_stopped:
            continue

        sec_ckpt, _ = get_section_checkpoint(json_filename, sec)
        if sec_ckpt and normalize_link(sec_ckpt) == norm:
            print(f"  [STOP] {sec} reached section checkpoint")
            section_stopped.add(sec)
            continue
        if norm in known_previous:
            print(f"  [STOP] {sec} already in previous file")
            section_stopped.add(sec)
            continue
        if norm in seen_this_run:
            continue
        if not art.get("title") and not art.get("description") and not art.get("summary"):
            print(f"  [SKIP] Empty row: {link[:80]}")
            continue

        print(f"  [INFO] New: {link[:80]}...")
        new_articles.append(art)
        seen_this_run.add(norm)
        print(f"  [+] {(art.get('title') or '')[:70]}")

        if reached_incremental_limit(len(new_articles), bootstrap=bootstrap):
            label_lim = "Bootstrap" if bootstrap else "Run safety"
            print(f"[INCREMENTAL] {label_lim} limit ({max_articles}) reached.")
            cap_hit = True
            break

    for name in section_keys:
        links = section_links.get(name, [])
        if not links:
            continue
        head_url = links[0]
        head_title = ""
        for a in all_feed:
            if a.get("link") == head_url or normalize_link(a.get("link", "")) == normalize_link(head_url):
                head_title = a.get("title", "") or ""
                break
        update_section_checkpoints(json_filename, {name: (head_url, head_title)})
        print(f"  [CKPT] {name} newest in feed: {head_url[:70]}")

    for name in section_keys:
        sec_ckpt, _ = get_section_checkpoint(json_filename, name)
        links = section_links.get(name, [])
        if not sec_ckpt and links:
            update_section_checkpoints(json_filename, {name: (links[0], "")})
            print(f"[SEED] {name}: {links[0][:80]}")

    print(f"\n[INCREMENTAL] New articles: {len(new_articles)}")
    save_replace_only(json_filename, new_articles)
    return len(new_articles)


def _page_is_cloudflare(driver) -> bool:
    title = (driver.title or "").lower()
    return "just a moment" in title or "attention required" in title


def main_incremental_selenium() -> int:
    """Per-section checkpoints — Selenium (local / non-blocked environments)."""
    json_filename = data_json_path("dinamina_latest_news.json")
    section_keys = [c[0] for c in DINAMINA_CATEGORIES]
    bootstrap = not any(get_section_checkpoint(json_filename, k)[0] for k in section_keys)
    max_articles = incremental_fetch_limit(bootstrap=bootstrap)

    known_previous = load_known_links(json_filename)
    if known_previous:
        print(f"[INCREMENTAL] Skipping {len(known_previous)} URL(s) from previous file")

    print("[INCREMENTAL] Dinamina — per-section checkpoints")
    if bootstrap:
        print(f"[INCREMENTAL] No checkpoint; bootstrap max {max_articles} articles")
    else:
        print(f"[INCREMENTAL] Run safety cap: {max_articles} new articles")

    driver = _create_dinamina_driver()
    driver.set_page_load_timeout(60)

    section_links: dict[str, list[str]] = {}
    for name, url in DINAMINA_CATEGORIES:
        print(f"\n[PHASE 1] {name}: {url}")
        try:
            driver.get(url.rstrip("/") + "/")
            links = get_category_article_links(driver)
            section_links[name] = links
            print(f"  {len(links)} links")
        except Exception as e:
            print(f"  [ERROR] {e}")
            section_links[name] = []

    new_articles: list[dict] = []
    seen_this_run: set[str] = set()
    cap_hit = False

    for name, _url in DINAMINA_CATEGORIES:
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
    save_replace_only(json_filename, new_articles)
    return len(new_articles)


def main_incremental() -> int:
    """RSS on CI (Cloudflare); Selenium locally with RSS fallback."""
    if os.getenv("CI", "").lower() in ("1", "true"):
        print("[INCREMENTAL] CI detected — using RSS feed")
        return main_incremental_rss()

    count = main_incremental_selenium()
    if count == 0:
        json_filename = data_json_path("dinamina_latest_news.json")
        try:
            driver = _create_dinamina_driver()
            driver.get(DINAMINA_CATEGORIES[0][1])
            time.sleep(2)
            cf = _page_is_cloudflare(driver)
            driver.quit()
        except Exception:
            cf = True
        if cf:
            print("[INCREMENTAL] Cloudflare detected — falling back to RSS")
            return main_incremental_rss()
    return count


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

    scraped_urls = set()
    all_articles = []
    total_articles_in_range = 0
    total_articles_outside_range = 0

    for category, cat_url in DINAMINA_CATEGORIES:
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
            
            main(start_date, end_date)
        except ValueError as e:
            print(f"[ERROR] Invalid date format. Use YYYY-MM-DD. Error: {e}")
            print("[INFO] Example: python dinamina_selenium_json.py 2026-02-02 2026-02-03")
    else:
        main()