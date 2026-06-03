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
from urllib.parse import urljoin, urlparse
import os
import sys
from datetime import datetime, timedelta

# Reconfigure stdout/stderr to use UTF-8 to prevent UnicodeEncodeError on Windows
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except AttributeError:
    pass


def setup_driver():
    """Initializes and returns a Selenium WebDriver with stealth settings."""
    chrome_options = Options()
    chrome_options.page_load_strategy = 'eager'
    chrome_options.add_argument("--headless")  # Runs Chrome in the background
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return driver

def parse_article_date(date_str):
    """Parse article date string to datetime object."""
    if not date_str or date_str == 'N/A':
        return None
    
    try:
        # Ceylon Today uses ISO format: 2025-07-18T02:03:00+05:30
        if 'T' in date_str:
            # Remove timezone info for parsing: 2025-07-18T02:03:00+05:30 -> 2025-07-18T02:03:00
            clean_date_str = date_str.split('+')[0] if '+' in date_str else date_str
            # Replace T with space: 2025-07-18T02:03:00 -> 2025-07-18 02:03:00
            clean_date_str = clean_date_str.replace('T', ' ')
            return datetime.strptime(clean_date_str, "%Y-%m-%d %H:%M:%S")
        else:
            # Fallback for other formats
            return datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
    except Exception as e:
        try:
            # More robust fallback - try to extract just the date part
            if 'T' in date_str:
                date_part = date_str.split('T')[0]  # Get just YYYY-MM-DD
                return datetime.strptime(date_part, "%Y-%m-%d")
            else:
                return datetime.strptime(date_str.split(' ')[0], "%Y-%m-%d")
        except Exception as e2:
            print(f"  [WARNING] Could not parse date '{date_str}': {e}, {e2}")
            return None

def is_article_in_date_range(article_date_str, start_date, end_date):
    """Check if article date falls within the specified range."""
    article_date = parse_article_date(article_date_str)
    if not article_date:
        return True  # Include articles with unparseable dates
    
    # Convert to date only for comparison
    article_date = article_date.date()
    return start_date <= article_date <= end_date

def extract_image_url_strategies(soup, base_url):
    """Try multiple strategies to extract a real image URL from the article page."""
    
    strategies = [
        # Strategy 1: OpenGraph image meta tag
        lambda: soup.find('meta', property='og:image'),
        
        # Strategy 2: Twitter card image
        lambda: soup.find('meta', name='twitter:image'),
        
        # Strategy 3: Article content images
        lambda: soup.select_one('.td-post-content img'),
        
        # Strategy 4: Featured image in post header
        lambda: soup.select_one('.td-post-featured-image img'),
        
        # Strategy 5: Any img in article wrapper
        lambda: soup.select_one('.td-main-content-wrap img'),
        
        # Strategy 6: Post thumbnail
        lambda: soup.select_one('.td-post-thumb img'),
        
        # Strategy 7: Entry thumb image
        lambda: soup.select_one('.entry-thumb img')
    ]
    
    for i, strategy in enumerate(strategies, 1):
        try:
            element = strategy()
            if element:
                # Extract URL from different possible attributes
                img_url = None
                if element.name == 'meta':
                    img_url = element.get('content')
                elif element.name == 'img':
                    img_url = element.get('src') or element.get('data-src') or element.get('data-original')
                
                if img_url:
                    # Resolve relative URLs
                    if img_url.startswith('//'):
                        img_url = 'https:' + img_url
                    elif img_url.startswith('/'):
                        img_url = urljoin(base_url, img_url)
                    elif not img_url.startswith('http'):
                        img_url = urljoin(base_url, img_url)
                    
                    # Validate URL
                    if is_valid_image_url(img_url):
                        print(f"  [INFO] Strategy {i} found valid image: {img_url}")
                        return img_url
                    else:
                        print(f"  [WARNING] Strategy {i} found invalid image: {img_url}")
        except Exception as e:
            print(f"  [ERROR] Strategy {i} failed: {e}")
            continue
    
    print("  [ERROR] No valid image found with any strategy")
    return "N/A"

def is_valid_image_url(url):
    """Quick validation of image URL."""
    if not url or url in ['N/A', '', 'null']:
        return False
    
    # Check if URL looks reasonable
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return False
    
    # Check for common image file extensions or image-related paths
    url_lower = url.lower()
    image_indicators = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '/image', '/img', '/photo', '/picture']
    
    return any(indicator in url_lower for indicator in image_indicators)

def get_enhanced_article_description(driver, article_url):
    """Enhanced description extraction with multiple fallback strategies."""
    try:
        print(f"  [LINK] Fetching: {article_url}")
        driver.get(article_url)
        
        # Wait for main content with multiple possible selectors
        selectors_to_wait = [".td-post-content", ".post-content", ".entry-content", "article"]
        content_loaded = False
        
        for selector in selectors_to_wait:
            try:
                WebDriverWait(driver, 8).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, selector))
                )
                content_loaded = True
                break
            except:
                continue
        
        if not content_loaded:
            print("  [WARNING] Content selectors not found, proceeding anyway...")
        
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        
        # Extract image URL using multiple strategies
        image_url = extract_image_url_strategies(soup, article_url)
        
        # Extract description with multiple fallback strategies
        description_strategies = [
            lambda: soup.find('div', class_='td-post-content'),
            lambda: soup.find('div', class_='post-content'),
            lambda: soup.find('div', class_='entry-content'),
            lambda: soup.find('article'),
            lambda: soup.find('div', class_='content')
        ]
        
        description = "Description not available."
        
        for strategy in description_strategies:
            try:
                content_div = strategy()
                if content_div:
                    paragraphs = content_div.find_all('p')
                    if paragraphs:
                        description = ' '.join([p.get_text(strip=True) for p in paragraphs if p.get_text(strip=True)])
                        if description:
                            # Limit description length to reasonable size
                            if len(description) > 2000:
                                description = description[:2000] + "..."
                            break
            except:
                continue
        
        print(f"  [INFO] Description length: {len(description)} chars")
        print(f"  [IMAGE] Image URL: {image_url}")
        
        return description, image_url
        
    except Exception as e:
        print(f"  [ERROR] Could not process article URL {article_url}: {e}")
        return "Error fetching description.", "N/A"

def main(start_date=None, end_date=None):
    """Main function to orchestrate the enhanced scraping process with date filtering."""
    
    # Default to today and yesterday if no dates provided
    if not start_date or not end_date:
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=1)
        print(f"[DATE] No date range provided, using default: {start_date} to {end_date}")
    else:
        print(f"[DATE] Scraping articles from {start_date} to {end_date}")
    
    print("[INFO] Starting Ceylon Today News scraper with date range filtering...")
    
    driver = setup_driver()
    max_pages = 20
    articles_data = []
    articles_in_range = 0
    articles_outside_range = 0
    max_consecutive_old_pages = 3
    categories = ["news", "columns", "features", "sports", "world", "business"]
    scraped_urls = set()

    # --- Step 1: Scrape basic info from list pages with date filtering ---
    print(f"\n[INFO] Step 1: Scraping article lists with date filtering...")
    
    for category in categories:
        category_base_url = f"https://ceylontoday.lk/category/ceylon-today-daily/{category}/"
        print(f"\n{'='*60}")
        print(f"[INFO] Scraping category: {category} ({category_base_url})")
        print(f"{'='*60}")
        consecutive_old_pages = 0
        
        for page_num in range(1, max_pages + 1):
            url = f"{category_base_url}page/{page_num}/" if page_num > 1 else category_base_url
            print(f"[INFO] Scraping list page {page_num}: {url}")
            
            try:
                driver.get(url)
                # Wait for the main article container to load
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.CLASS_NAME, "tdb_loop"))
                )
                
                soup = BeautifulSoup(driver.page_source, 'html.parser')
                articles = soup.select('div.tdb_module_loop.td_module_wrap')
                
                page_articles_in_range = 0
                page_articles_total = 0
                page_has_newer_articles = False
                
                for article in articles:
                    title_tag = article.select_one('h3.entry-title a')
                    if not title_tag:
                        continue
                    
                    link = title_tag.get('href')
                    title = title_tag.get_text(strip=True)
                    
                    date_tag = article.select_one('time.entry-date')
                    date = date_tag['datetime'] if date_tag else 'N/A'
                    
                    page_articles_total += 1
                    
                    if link in scraped_urls:
                        print(f"  [SKIP] Already scraped duplicate link: {link[:60]}...")
                        continue
                    
                    # Parse date for range checks
                    parsed_date = parse_article_date(date)
                    if not parsed_date:
                        articles_data.append({
                            'title': title,
                            'link': link,
                            'date': date,
                            'image_url': 'N/A',
                            'description': ''
                        })
                        scraped_urls.add(link)
                        page_articles_in_range += 1
                        articles_in_range += 1
                        print(f"  [INFO] Article in range (unparseable date): {title[:50]}...")
                    else:
                        article_date_only = parsed_date.date()
                        if start_date <= article_date_only <= end_date:
                            articles_data.append({
                                'title': title,
                                'link': link,
                                'date': date,
                                'image_url': 'N/A',
                                'description': ''
                            })
                            scraped_urls.add(link)
                            page_articles_in_range += 1
                            articles_in_range += 1
                            print(f"  [INFO] Article in range: {title[:50]}...")
                        elif article_date_only > end_date:
                            page_has_newer_articles = True
                            articles_outside_range += 1
                            print(f"  [SKIP] Article is newer than range ({date}): {title[:50]}...")
                        else:
                            articles_outside_range += 1
                            print(f"  [SKIP] Article is older than range ({date}): {title[:50]}...")
                
                print(f"  [INFO] Page {page_num}: {page_articles_in_range}/{page_articles_total} articles in date range")
                
                # Check if we should continue to next page
                if page_articles_total == 0:
                    break
                    
                if page_articles_in_range == 0 and not page_has_newer_articles:
                    # Page contains only older articles, increment old pages counter
                    consecutive_old_pages += 1
                    print(f"  [WARNING] Only older articles on this page ({consecutive_old_pages}/{max_consecutive_old_pages} consecutive)")
                    if consecutive_old_pages >= max_consecutive_old_pages:
                        print(f"  [ERROR] Stopping category {category}: {max_consecutive_old_pages} consecutive pages with only older articles")
                        break
                else:
                    consecutive_old_pages = 0  # Reset counter
                    
            except Exception as e:
                print(f"  [ERROR] Failed to scrape page {url}: {e}")
                if page_num == 1:
                    print(f"  [WARNING] Category {category} page 1 failed, skipping category...")
                    break
                continue

    print(f"\n[INFO] Date filtering summary:")
    print(f"  [INFO] Articles in date range: {articles_in_range}")
    print(f"  [INFO] Articles outside range: {articles_outside_range}")
    print(f"  [INFO] Total articles found: {len(articles_data)}")

    if not articles_data:
        print(f"[WARNING] No articles found in the specified date range ({start_date} to {end_date})")
        print("[INFO] Proceeding to generate empty JSON.")

    # --- Step 2: Visit each link to get the full description and real image URL ---
    print(f"\n[INFO] Step 2: Processing {len(articles_data)} articles for content and images...")
    
    successful_articles = 0
    for i, article in enumerate(articles_data):
        print(f"\n[INFO] Processing article {i+1}/{len(articles_data)}:")
        print(f"  [TITLE] {article['title'][:60]}...")
        print(f"  [DATE] {article['date']}")
        
        if article['link']:
            description, image_url = get_enhanced_article_description(driver, article['link'])
            article['description'] = description
            article['image_url'] = image_url
            
            if description != "Error fetching description.":
                successful_articles += 1
            
            # Polite delay between requests
            time.sleep(1)
        else:
            print("  [WARNING] No link available for this article")

    driver.quit()
    print(f"\n[INFO] Browser closed. Successfully processed {successful_articles}/{len(articles_data)} articles")

    # --- Step 3: Save all data to JSON file in data directory (even if empty) ---
    # Ensure we save to the data directory (create if it doesn't exist)
    data_dir = "data"
    os.makedirs(data_dir, exist_ok=True)

    json_filename = os.path.join(data_dir, "ceylontoday_finance.json")

    try:
        if not articles_data:
            print(f" [INFO] 0 articles scraped. Preserving existing data in {json_filename} intact.")
        else:
            with open(json_filename, 'w', encoding='utf-8') as jsonfile:
                json.dump(articles_data, jsonfile, ensure_ascii=False, indent=2)

        if not articles_data:
            print(f"[WARNING] No articles were scraped for the date range ({start_date} to {end_date})")
            print(f"[INFO] Saved empty JSON array to {json_filename}")
            print("[INFO] Try expanding your date range or check if Ceylon Today has articles for those dates")
        else:
            print(f"\n[INFO] Scraping complete!")
            print(f"[INFO] Saved {len(articles_data)} articles to {json_filename}")
            print(f"[INFO] Success rate: {successful_articles}/{len(articles_data)} articles")
            print(f"[INFO] Date range: {start_date} to {end_date}")

            # Count articles with real images
            real_images = sum(1 for article in articles_data if article['image_url'] not in ['N/A', '', 'null'])
            print(f"[IMAGE] Articles with images: {real_images}/{len(articles_data)}")

    except Exception as e:
        print(f"[ERROR] Error saving to JSON file: {e}")

if __name__ == "__main__":
    _scraper_dir = os.path.dirname(os.path.abspath(__file__))
    if _scraper_dir not in sys.path:
        sys.path.insert(0, _scraper_dir)
    from incremental import is_incremental_mode

    if is_incremental_mode():
        from incremental_outlets import run_incremental_for_module

        run_incremental_for_module("ceylontoday_selenium_json")
        sys.exit(0)

    # Check if date range is provided as command line arguments
    if len(sys.argv) >= 3:
        try:
            start_date = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
            end_date = datetime.strptime(sys.argv[2], "%Y-%m-%d").date()
            main(start_date, end_date)
        except ValueError as e:
            print(f"[ERROR] Invalid date format. Use YYYY-MM-DD. Error: {e}")
            print("[INFO] Example: python ceylontoday_selenium_json.py 2025-01-15 2025-01-20")
    else:
        main()  # Use default date range 