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
import requests
import os
import sys
from datetime import datetime, timedelta
import re

def extract_article_metadata(driver):
    """Extract detailed metadata from current EconomyNext article page."""
    try:
        # Wait for content to load dynamically
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CLASS_NAME, "story-page-text-content"))
            )
        except Exception as e:
            print(f"     Warning: Timeout waiting for story-page-text-content: {e}")
            time.sleep(2)  # Fallback sleep
        
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        # Extract publication date from meta tag or structured data
        date_published = None
        
        # Strategy 1: Try article:published_time meta tag
        date_meta = soup.find('meta', attrs={'property': 'article:published_time'})
        if date_meta and date_meta.has_attr('content'):
            try:
                # Parse format like "6:23 pm,Thursday July 17, 2025" or "10:14 am,Thursday May 21, 2026"
                date_str = date_meta['content']
                # Extract the date part (after the comma)
                if ',' in date_str:
                    date_part = date_str.split(',', 1)[1].strip()
                    for fmt in ["%A %B %d, %Y", "%A %b %d, %Y"]:
                        try:
                            date_published = datetime.strptime(date_part, fmt)
                            print(f"     Found date from meta tag: {date_meta['content']}")
                            break
                        except ValueError:
                            continue
            except Exception as e:
                print(f"     Error parsing meta date '{date_meta['content']}': {e}")
        
        # Strategy 2: Try structured data (JSON-LD)
        if not date_published:
            schema_scripts = soup.find_all('script', type='application/ld+json')
            for script in schema_scripts:
                try:
                    data = json.loads(script.string)
                    if isinstance(data, dict) and 'datePublished' in data:
                        date_str = data['datePublished']
                        # Parse ISO format: "2025-07-17T18:23:03+00:00"
                        date_published = datetime.fromisoformat(date_str.replace('Z', '+00:00').replace('+00:00', ''))
                        print(f"     Found date from structured data: {date_str}")
                        break
                except (json.JSONDecodeError, ValueError, TypeError) as e:
                    continue
        
        if not date_published:
            print(f"     No publication date found")
        
        # Extract image URL from OpenGraph meta tag
        image_url = ""
        og_image = soup.find('meta', attrs={'property': 'og:image'})
        if og_image and og_image.has_attr('content'):
            image_url = og_image['content']
            print(f"     Found image: {image_url[:60]}...")

        # Extract description from article page using robust class selector
        description = ""
        try:
            desc_elements = driver.find_elements(By.CLASS_NAME, "story-page-text-content")
            main_element = None
            for el in desc_elements:
                el_class = el.get_attribute("class") or ""
                if "most-recent-article-text" not in el_class:
                    main_element = el
                    break
            
            # Fallback to the first element if all or none have the class
            if not main_element and desc_elements:
                main_element = desc_elements[0]
                
            if main_element:
                paragraphs = []
                p_elements = main_element.find_elements(By.TAG_NAME, "p")
                if p_elements:
                    for p in p_elements:
                        p_text = p.text.strip()
                        if p_text and p_text not in paragraphs:
                            paragraphs.append(p_text)
                else:
                    element_text = main_element.text.strip()
                    if element_text and element_text not in paragraphs:
                        paragraphs.append(element_text)
                
                if paragraphs:
                    description = "\n\n".join(paragraphs)
                    print(f"     Found description from class story-page-text-content: {len(description)} characters")
        except Exception as e:
            print(f"     Error finding content via class name: {e}")

        # Fallback XPath patterns if class selector didn't return enough text
        if not description or len(description) < 100:
            description_xpaths = [
                "/html/body/div[3]/div[2]/div/div/div[5]/div",
                "/html/body/div[3]/div[2]/div/div/div[6]/div"
            ]

            for xpath in description_xpaths:
                try:
                    desc_element = driver.find_element(By.XPATH, xpath)
                    xpath_desc = desc_element.text.strip()
                    if xpath_desc and len(xpath_desc) > len(description):
                        description = xpath_desc
                        print(f"     Found better description from XPath: {len(description)} characters")
                        break
                except:
                    continue

        # Fallback: Try BeautifulSoup extraction of main story page text content
        if not description:
            try:
                print("     Attempting fallback extraction using BeautifulSoup class matching...")
                main_divs = soup.find_all('div', class_='story-page-text-content')
                paragraphs = []
                for div in main_divs:
                    classes = div.get('class') or []
                    if 'most-recent-article-text' not in classes:
                        p_tags = div.find_all('p')
                        for p in p_tags:
                            text = p.get_text().strip()
                            if text and text not in paragraphs:
                                paragraphs.append(text)
                        if not paragraphs:
                            div_text = div.get_text().strip()
                            if div_text:
                                paragraphs.append(div_text)
                        break
                if paragraphs:
                    description = "\n\n".join(paragraphs)
                    print(f"     Found description via BS4 fallback: {len(description)} characters")
            except Exception as e:
                print(f"     BS4 description fallback failed: {e}")

        # Fallback: Try OpenGraph description if above methods didn't work
        if not description:
            og_description = soup.find('meta', attrs={'property': 'og:description'})
            if og_description and og_description.has_attr('content'):
                description = og_description['content']
                print(f"     Found description from meta tag: {description[:60]}...")
            else:
                twitter_description = soup.find('meta', attrs={'name': 'twitter:description'})
                if twitter_description and twitter_description.has_attr('content'):
                    description = twitter_description['content']
                    print(f"     Found description from twitter meta tag: {description[:60]}...")
        
        # Extract title from the page
        title = ""
        og_title = soup.find('meta', attrs={'property': 'og:title'})
        if og_title and og_title.has_attr('content'):
            title = og_title['content']
        
        # Get current URL
        current_url = driver.current_url
        
        return {
            'date_published': date_published,
            'image_url': image_url,
            'description': description,
            'title': title,
            'link': current_url
        }
    
    except Exception as e:
        print(f"     Error extracting metadata: {e}")
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

def process_homepage_articles(driver, start_date, end_date, processed_urls):
    """Extract and process recent article links from the homepage."""
    print(f"\n[INFO] Loading homepage: https://economynext.com/")
    driver.get("https://economynext.com/")
    time.sleep(5)
    
    try:
        soup = BeautifulSoup(driver.page_source, 'html.parser')
    except Exception as e:
        print(f"   Error parsing page source: {e}")
        return []
        
    # Extract unique article links matching post ID pattern
    links_with_ids = []
    for a in soup.find_all('a', href=True):
        href = a['href'].strip()
        if not href.startswith('http'):
            # Convert relative path if any
            if href.startswith('/'):
                href = "https://economynext.com" + href
            else:
                continue
        
        # Match pattern: URL ending with -ID/
        match = re.search(r'-(\d+)/?$', href)
        if match:
            post_id = int(match.group(1))
            if (href, post_id) not in links_with_ids:
                links_with_ids.append((href, post_id))
                
    if not links_with_ids:
        print("   No article links with IDs found on the homepage.")
        return []
        
    print(f"   Found {len(links_with_ids)} unique articles on homepage")
    
    max_id = max(post_id for _, post_id in links_with_ids)
    threshold = max_id - 800
    filtered_links = [url for url, pid in links_with_ids if pid >= threshold]
    print(f"   Filtered to {len(filtered_links)} articles with ID >= {threshold}")
    
    articles_found = []
    
    for idx, article_link in enumerate(filtered_links):
        # Normalize URL to avoid duplicates with/without trailing slash
        normalized_url = article_link.rstrip('/')
        if normalized_url in processed_urls:
            continue
            
        processed_urls.add(normalized_url)
        processed_urls.add(normalized_url + '/')
        
        print(f"\n   Homepage Article {idx+1}/{len(filtered_links)}: {article_link}")
        
        try:
            driver.get(article_link)
            time.sleep(2)
            
            metadata = extract_article_metadata(driver)
            
            # Retry if description is empty or short
            if not metadata['description'] or len(metadata['description']) < 100:
                print(f"     [RETRY] Empty/short description. Retrying page load...")
                driver.get(article_link)
                time.sleep(5)
                metadata = extract_article_metadata(driver)
                
            final_date = metadata['date_published']
            
            if final_date is None:
                print(f"     [SKIP] No date found for article, skipping")
                continue
                
            if not is_article_in_date_range(final_date, start_date, end_date):
                print(f"     [SKIP] Date {final_date.strftime('%Y-%m-%d')} is outside range")
                continue
                
            standardized_date = final_date.strftime("%Y-%m-%d %H:%M:%S")
            
            articles_found.append({
                'title': metadata['title'],
                'link': article_link,
                'summary': metadata['description'],
                'date': standardized_date,
                'image_url': metadata['image_url'],
                'date_source': "Article page (Homepage link)"
            })
            print(f"     [ADDED] Article is in range and added successfully")
            
        except Exception as e:
            print(f"     Error processing homepage article: {e}")
            continue
            
    print(f"\n  Homepage scraping summary: Found {len(articles_found)} articles in range")
    return articles_found

def process_articles_from_list_page(driver, list_url, start_date, end_date, processed_urls):
    """Process articles from a single EconomyNext list page using new-tab navigation."""
    print(f"\n[INFO] Processing articles from: {list_url}")

    # Navigate to list page
    driver.get(list_url)

    try:
        # Wait for the main article container to load
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CLASS_NAME, "story-grid-single-story"))
        )
        print(f"   List page loaded successfully")
    except Exception as e:
        print(f"   Timeout waiting for articles: {e}")
        # Check if Cloudflare challenge is present
        page_source_lower = driver.page_source.lower()
        if 'cloudflare' in page_source_lower or 'challenge' in page_source_lower or 'checking your browser' in page_source_lower:
            print(f"   [WARNING] Cloudflare challenge detected! Waiting longer...")
            time.sleep(10)
            try:
                WebDriverWait(driver, 20).until(
                    EC.presence_of_element_located((By.CLASS_NAME, "story-grid-single-story"))
                )
                print(f"   List page loaded after Cloudflare wait")
            except:
                return [], 0, 0
        else:
            return [], 0, 0

    articles_found = []
    articles_in_range = 0
    articles_outside_range = 0
    consecutive_outside_range = 0
    max_consecutive_outside = 3

    # Prefetch article metadata from list page elements to avoid stale reference errors during navigation
    card_elements = driver.find_elements(By.CLASS_NAME, "story-grid-single-story")
    print(f"   Found {len(card_elements)} article cards on listing page")
    
    prefetched_cards = []
    for idx, card in enumerate(card_elements):
        try:
            # Find link & title
            try:
                heading_el = card.find_element(By.CSS_SELECTOR, "h3.recent-top-header a")
            except:
                try:
                    heading_el = card.find_element(By.CSS_SELECTOR, "h3 a")
                except:
                    heading_el = card.find_element(By.TAG_NAME, "a")
            
            title = heading_el.text.strip()
            article_link = heading_el.get_attribute('href')
            
            # Find date
            date_text = ""
            try:
                date_el = card.find_element(By.CLASS_NAME, "article-publish-date")
                date_text = date_el.text.strip()
            except:
                pass

            # Find image
            image_url = ""
            try:
                img_el = card.find_element(By.TAG_NAME, "img")
                image_url = img_el.get_attribute('src')
            except:
                try:
                    amp_img_el = card.find_element(By.TAG_NAME, "amp-img")
                    image_url = amp_img_el.get_attribute('src')
                except:
                    pass
            
            prefetched_cards.append({
                'title': title,
                'link': article_link,
                'date_text': date_text,
                'image_url': image_url
            })
        except Exception as e:
            print(f"   Error pre-fetching card {idx+1}: {e}")

    # Process each prefetched card
    for idx, prefetched in enumerate(prefetched_cards):
        try:
            title = prefetched['title']
            article_link = prefetched['link']
            date_text = prefetched['date_text']
            image_url = prefetched['image_url']

            if not article_link:
                continue

            normalized_url = article_link.rstrip('/')
            if normalized_url in processed_urls:
                print(f"\n   Article {idx+1}: Skipping (already processed)")
                articles_in_range += 1  # Count as in-range to prevent premature pagination stops
                continue

            print(f"\n   Article {idx+1}: {title[:60]}...")
            print(f"     Link: {article_link}")
            print(f"     Date text: {date_text}")

            # Parse the date from the date text
            article_date = None
            if date_text:
                try:
                    date_text_clean = date_text.strip()
                    date_formats = [
                        "%B %d, %Y",      # December 15, 2024
                        "%b %d, %Y",      # Dec 15, 2024
                        "%Y-%m-%d",       # 2024-12-15
                        "%d %B %Y",       # 15 December 2024
                        "%d %b %Y",       # 15 Dec 2024
                    ]

                    for fmt in date_formats:
                        try:
                            article_date = datetime.strptime(date_text_clean, fmt)
                            print(f"     Parsed date: {article_date.strftime('%Y-%m-%d')}")
                            break
                        except:
                            continue
                except Exception as e:
                    print(f"     Error parsing date '{date_text}': {e}")

            # Check if article is in date range
            if article_date is not None:
                if not is_article_in_date_range(article_date, start_date, end_date):
                    article_date_str = article_date.strftime('%Y-%m-%d')
                    print(f"     [SKIP] Article outside date range: {article_date_str}")
                    articles_outside_range += 1
                    consecutive_outside_range += 1

                    # Check if article is significantly older than target range
                    days_before_range = (start_date - article_date.date()).days
                    if days_before_range > 2:
                        print(f"     Article is {days_before_range} days before target range - stopping immediately")
                        break

                    if consecutive_outside_range >= max_consecutive_outside:
                        print(f"\n   Stopping: {max_consecutive_outside} consecutive articles outside date range")
                        break
                    continue

            # Visit the article page directly
            final_date = article_date
            final_title = title
            final_image = image_url
            final_description = ""
            extraction_successful = False

            try:
                print(f"     Visiting article page directly...")
                driver.get(article_link)
                time.sleep(2)

                # Extract metadata from article page
                metadata = extract_article_metadata(driver)
                
                # Retry once if description is still empty (could be a slow load issue)
                if not metadata['description'] or len(metadata['description']) < 100:
                    print(f"     [RETRY] Description was empty/short ({len(metadata['description']) if metadata['description'] else 0} chars). Retrying page load...")
                    driver.get(article_link)
                    time.sleep(5)
                    metadata = extract_article_metadata(driver)

                if metadata['date_published']:
                    final_date = metadata['date_published']
                if metadata['title']:
                    final_title = metadata['title']
                if metadata['image_url']:
                    final_image = metadata['image_url']
                if metadata['description']:
                    final_description = metadata['description']
                    extraction_successful = True

            except Exception as e:
                print(f"     Error loading article page: {e}")

            # Recheck date if it was not present on list page
            if article_date is None:
                if final_date is None:
                    print(f"     [SKIP] Still no date found for article, skipping")
                    consecutive_outside_range += 1
                    continue
                elif not is_article_in_date_range(final_date, start_date, end_date):
                    print(f"     [SKIP] Date {final_date.strftime('%Y-%m-%d')} is outside range")
                    articles_outside_range += 1
                    consecutive_outside_range += 1
                    continue

            # Standardized date format
            standardized_date = final_date.strftime("%Y-%m-%d %H:%M:%S")

            articles_found.append({
                'title': final_title,
                'link': article_link,
                'summary': final_description,
                'date': standardized_date,
                'image_url': final_image,
                'date_source': "Article page" if final_date != article_date else "List page"
            })
            processed_urls.add(normalized_url)
            processed_urls.add(normalized_url + '/')
            articles_in_range += 1
            consecutive_outside_range = 0

        except Exception as e:
            print(f"     Error with article prefetched card: {e}")
            continue

    print(f"\n  Page summary: {articles_in_range} in range, {articles_outside_range} outside range")
    return articles_found, articles_in_range, articles_outside_range

def main(start_date=None, end_date=None):
    """Enhanced main function with date range filtering and click-back navigation."""
    
    # Default to yesterday and today if no dates provided
    if not start_date or not end_date:
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=1)
        print(f"[DATE] No date range provided, using default: {start_date} to {end_date}")
    else:
        print(f"[DATE] Scraping articles from {start_date} to {end_date}")
    
    print("[INFO] Starting Enhanced EconomyNext scraper with date filtering...")
    print("=" * 50)
    
    if USE_UNDETECTED:
        print("[INFO] Using undetected-chromedriver to bypass Cloudflare...")
        options = uc.ChromeOptions()
        options.page_load_strategy = 'eager'
        
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

    # Set page load timeout to prevent hanging
    driver.set_page_load_timeout(60)
    print(f"[INFO] Page load timeout set to 60 seconds")

    
    base_url = "https://economynext.com/more-news/"
    all_articles = []
    processed_urls = set()
    total_articles_in_range = 0
    total_articles_outside_range = 0
    consecutive_empty_pages = 0
    max_consecutive_empty = 3
    max_pages = 8  # Process more pages since we can stop early
    
    try:
        # First, scrape articles directly from the homepage
        try:
            homepage_articles = process_homepage_articles(driver, start_date, end_date, processed_urls)
            all_articles.extend(homepage_articles)
            total_articles_in_range += len(homepage_articles)
        except Exception as e:
            print(f"[ERROR] Error processing homepage articles: {e}")

        print(f"\n[INFO] Processing paginated articles page by page with date filtering...")
        
        for page_num in range(max_pages):
            if page_num == 0:
                list_url = base_url
            else:
                list_url = f"https://economynext.com/more-news/page/{page_num + 1}/"
            
            print(f"\n[INFO] Page {page_num + 1}:")
            
            try:
                articles, page_in_range, page_outside_range = process_articles_from_list_page(
                    driver, list_url, start_date, end_date, processed_urls
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
        
    finally:
        print("[INFO] Closing browser...")
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
        articles_with_images = sum(1 for article in all_articles if article.get('image_url') != '')
        print(f"[IMAGE] Articles with images: {articles_with_images}/{len(all_articles)} ({articles_with_images/len(all_articles)*100:.1f}%)")

        # Show sample titles
        print(f"\n[INFO] Sample articles:")
        for i, article in enumerate(all_articles[:3], 1):
            title = article.get('title', 'Unknown')[:80]
            print(f"   {i}. {title}...")

    # Enhanced file saving - save to ../data/ directory (even if empty)
    data_dir = 'data'
    os.makedirs(data_dir, exist_ok=True)

    json_filename = os.path.join(data_dir, 'economynext_latest_news.json')

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
            print(f"[INFO] Successfully saved {len(all_articles)} articles to {json_filename}")
            print(f"[INFO] File location: {os.path.abspath(json_filename)}")
            print(f"[INFO] Date range: {start_date} to {end_date}")

    except Exception as e:
        print(f"[ERROR] Error saving to file: {e}")
        # Fallback: save to current directory
        fallback_filename = 'economynext_latest_news.json'
        try:
            with open(fallback_filename, 'w', encoding='utf-8') as jsonfile:
                json.dump(all_articles, jsonfile, ensure_ascii=False, indent=2)
            print(f"[INFO] Fallback: Saved to {fallback_filename} in current directory")
        except Exception as e2:
            print(f"[ERROR] Fallback save also failed: {e2}")
    
    print("\n[SUCCESS] EconomyNext Enhanced Scraper completed successfully!")

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
            print(f" Invalid date format. Use YYYY-MM-DD. Error: {e}")
            print(" Example: python economynext_selenium_json.py 2025-01-18 2025-01-19")
    else:
        main()  # Use default date range 