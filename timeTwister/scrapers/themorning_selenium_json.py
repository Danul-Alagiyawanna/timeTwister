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
import sys
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse
import os

def extract_article_metadata(driver):
    """Extract detailed metadata from The Morning article page including exact publication date."""
    try:
        # Wait for page to load
        time.sleep(2)
        
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        base_url = driver.current_url
        
        # Extract publication date from meta tag
        date_published = None
        
        # Strategy 1: Try article:published_time meta tag (most reliable)
        date_meta = soup.find('meta', attrs={'property': 'article:published_time'})
        if date_meta and date_meta.has_attr('content'):
            try:
                # Parse ISO format: "2025-07-19T00:00:00+00:00Z"
                date_str = date_meta['content']
                # Handle timezone and Z suffix
                if date_str.endswith('Z'):
                    date_str = date_str[:-1] + '+00:00'
                date_published = datetime.fromisoformat(date_str.replace('Z', ''))
                print(f"     Found exact date: {date_meta['content']}")
            except ValueError as e:
                print(f"     Error parsing date '{date_meta['content']}': {e}")
        
        # Strategy 2: Try article:modified_time meta tag
        if not date_published:
            date_meta = soup.find('meta', attrs={'property': 'article:modified_time'})
            if date_meta and date_meta.has_attr('content'):
                try:
                    date_str = date_meta['content']
                    if date_str.endswith('Z'):
                        date_str = date_str[:-1] + '+00:00'
                    date_published = datetime.fromisoformat(date_str.replace('Z', ''))
                    print(f"     Found date from modified time: {date_meta['content']}")
                except ValueError as e:
                    print(f"     Error parsing modified date '{date_meta['content']}': {e}")
        
        # Strategy 3: Try to find date in page content (fallback)
        if not date_published:
            date_tag = soup.find('p', class_='text-grey-base')
            if date_tag:
                date_text = date_tag.get_text(strip=True)
                # Try to parse various date formats from page content
                import re
                date_patterns = [
                    (r'(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4})', "%d %b %Y"),
                    (r'(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})', "%d %B %Y"),
                    (r'(\d{4}-\d{1,2}-\d{1,2})', "%Y-%m-%d")
                ]
                
                for pattern, date_format in date_patterns:
                    match = re.search(pattern, date_text, re.IGNORECASE)
                    if match:
                        try:
                            date_published = datetime.strptime(match.group(1), date_format)
                            print(f"     Found date from content: {match.group(1)}")
                            break
                        except ValueError:
                            continue
        
        if not date_published:
            print(f"     No publication date found")
        
        # Title
        title = ""
        # Try OpenGraph title first
        og_title = soup.find('meta', property='og:title')
        if og_title and og_title.get('content'):
            title = og_title['content']
        else:
            # Fallback to page title
            title_tag = soup.find('h1', class_='font-heading')
            title = title_tag.get_text(strip=True) if title_tag else ''
        
        # Enhanced image extraction using existing function
        image_url = extract_image_url(soup, base_url)
        
        # Description/content with enhanced extraction
        description = ''
        
        # Try to get meta description first
        meta_desc = soup.find('meta', property='og:description')
        if meta_desc and meta_desc.get('content'):
            description = meta_desc.get('content')
            print(f"    [DESC] Found description: {description[:60]}...")
        
        # If no meta description, try content div
        if not description:
            content_div = soup.find('div', class_='prose-lg')
            if content_div:
                description = content_div.get_text(separator=' ', strip=True)[:500]  # Limit to 500 chars
                print(f"    [DESC] Found description (content): {description[:60]}...")
        
        # Fallback to any meta description
        if not description:
            meta_desc_name = soup.find('meta', attrs={'name': 'description'})
            if meta_desc_name and meta_desc_name.get('content'):
                description = meta_desc_name['content']
                print(f"     Found description (fallback): {description[:60]}...")
        
        return {
            'date_published': date_published,
            'title': title,
            'image_url': image_url,
            'description': description,
            'link': driver.current_url
        }
        
    except Exception as e:
        print(f"     Error extracting metadata: {e}")
        return {
            'date_published': None,
            'title': "",
            'image_url': "",
            'description': "",
            'link': driver.current_url if driver else ""
        }

def is_article_in_date_range(article_date, start_date, end_date):
    """Check if article date falls within the specified range."""
    if not article_date:
        return False  # Exclude articles without parseable dates for accuracy
    
    # Convert to date only for comparison
    article_date = article_date.date()
    return start_date <= article_date <= end_date



def get_main_article_links(driver):
    soup = BeautifulSoup(driver.page_source, 'html.parser')
    # Find all main news grid containers
    main_grids = soup.find_all('div', class_=['grid', 'grid-cols-8', 'gap-4', 'mb-10'])
    links = []
    seen = set()
    for grid in main_grids:
        for a in grid.find_all('a', href=True):
            href = a['href']
            if href.startswith('/articles/'):
                full_url = 'https://www.themorning.lk' + href
                if full_url not in seen:
                    seen.add(full_url)
                    links.append(full_url)
    return links

def extract_image_url(soup, base_url):
    """Extract image URL using multiple strategies"""
    
    # Strategy 1: OpenGraph meta tag (most reliable)
    meta_img = soup.find('meta', property='og:image')
    if meta_img and meta_img.get('content'):
        image_url = meta_img['content']
        if image_url and not image_url.startswith('data:'):
            print(f"    [IMAGE] Found OpenGraph image: {image_url[:60]}...")
            return urljoin(base_url, image_url)
    
    # Strategy 2: Twitter card image
    twitter_img = soup.find('meta', attrs={'name': 'twitter:image'})
    if twitter_img and twitter_img.get('content'):
        image_url = twitter_img['content']
        if image_url and not image_url.startswith('data:'):
            print(f"    [IMAGE] Found Twitter card image: {image_url[:60]}...")
            return urljoin(base_url, image_url)
    
    # Strategy 3: Main article image in content
    content_selectors = [
        'div.prose-lg img',
        'article img',
        '.article-content img',
        '.content img'
    ]
    
    for selector in content_selectors:
        img_tags = soup.select(selector)
        for img in img_tags:
            if img.get('src'):
                image_url = img['src']
                if not image_url.startswith('data:') and 'placeholder' not in image_url.lower():
                    full_url = urljoin(base_url, image_url)
                    print(f"    [IMAGE] Found content image: {full_url[:60]}...")
                    return full_url
    
    # Strategy 4: Featured image or hero image
    hero_selectors = [
        '.featured-image img',
        '.hero-image img',
        '.article-hero img',
        '.post-thumbnail img'
    ]
    
    for selector in hero_selectors:
        img = soup.select_one(selector)
        if img and img.get('src'):
            image_url = img['src']
            if not image_url.startswith('data:'):
                full_url = urljoin(base_url, image_url)
                print(f"    [IMAGE] Found hero image: {full_url[:60]}...")
                return full_url
    
    # Strategy 5: Any img tag with reasonable src (last resort)
    all_imgs = soup.find_all('img', src=True)
    for img in all_imgs:
        src = img['src']
        if (src and not src.startswith('data:') and 
            'logo' not in src.lower() and 
            'icon' not in src.lower() and
            'avatar' not in src.lower() and
            'placeholder' not in src.lower() and
            len(src) > 10):
            full_url = urljoin(base_url, src)
            print(f"     Found fallback image: {full_url[:60]}...")
            return full_url
    
    print("     No suitable image found")
    return ''



def main(start_date=None, end_date=None):
    """Enhanced main function with date range filtering."""
    
    # Default to yesterday and today if no dates provided
    if not start_date or not end_date:
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=1)
        print(f"[DATE] No date range provided, using default: {start_date} to {end_date}")
    else:
        print(f"[DATE] Scraping articles from {start_date} to {end_date}")
    
    print("[INFO] Starting The Morning scraper...")
    print("=" * 50)
    
    # Enhanced Chrome options for stealth browsing
    chrome_options = Options()
    chrome_options.page_load_strategy = 'eager'
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)

    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
    
    # Add stealth settings
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    
    categories = ["news", "opinion", "business", "features", "sports", "world"]
    article_links = []
    seen_links = set()
    
    for cat in categories:
        url = f"https://www.themorning.lk/categories/{cat}"
        print(f"\n[INFO] Navigating to: {url}")
        try:
            driver.get(url)
            time.sleep(3)
            print(" Getting article links...")
            links = get_main_article_links(driver)
            print(f" Found {len(links)} links in category '{cat}'")
            for link in links:
                if link not in seen_links:
                    seen_links.add(link)
                    article_links.append(link)
        except Exception as e:
            print(f"  [ERROR] Error getting links for category {cat}: {e}")
            
    print(f"\n[INFO] Found total of {len(article_links)} unique article links across all categories")
    
    if not article_links:
        print(" No article links found")
        driver.quit()
        return
    
    # Process articles one by one with smart stopping logic (like Daily Mirror)
    filtered_articles = []
    articles_in_range = 0
    articles_outside_range = 0
    consecutive_outside_range = 0
    max_consecutive_outside = 3  # Stop if 3 consecutive articles are outside range
    
    print(f"\n Processing articles one by one with smart stopping...")
    
    for i, link in enumerate(article_links, 1):
        print(f"\n Processing article {i}/{len(article_links)}: {link}")
        
        try:
            driver.get(link)
            time.sleep(2)
            
            # Extract metadata from the article page
            metadata = extract_article_metadata(driver)
            
            # Check if article is in date range
            article_date = metadata['date_published']
            
            if article_date is None:
                print(f"     No date found, skipping article")
                consecutive_outside_range += 1
            elif is_article_in_date_range(article_date, start_date, end_date):
                print(f"     Article is in date range!")
                
                # Standardized date format
                standardized_date = article_date.strftime("%Y-%m-%d %H:%M:%S")
                
                filtered_articles.append({
                    'title': metadata['title'],
                    'link': metadata['link'],
                    'description': metadata['description'],
                    'date': standardized_date,
                    'image_url': metadata['image_url'],
                    'date_source': f"Meta tag: {standardized_date}"
                })
                articles_in_range += 1
                consecutive_outside_range = 0  # Reset counter
            else:
                article_date_str = article_date.strftime('%Y-%m-%d')
                print(f"     Article outside date range: {article_date_str}")
                articles_outside_range += 1
                
                # Only treat as consecutive outside range (for stopping) if it is older than our target range
                if article_date.date() < start_date:
                    consecutive_outside_range += 1
                else:
                    # Reset counter for newer articles so we can keep scanning down to older ones
                    consecutive_outside_range = 0
                
                # Check if article is significantly older than our target range
                days_before_range = (start_date - article_date.date()).days
                if days_before_range > 2:
                    print(f"     Article is {days_before_range} days before target range - stopping immediately")
                    break
            
            # Check if we should stop (too many consecutive articles outside range)
            if consecutive_outside_range >= max_consecutive_outside:
                print(f"\n   Stopping: {max_consecutive_outside} consecutive articles outside date range")
                break
            
            time.sleep(1)  # Be polite between requests
            
        except Exception as e:
            print(f"     Error processing article: {e}")
            continue
    
    driver.quit()
    
    # Enhanced results summary
    print(f"\n Processing summary:")
    print(f"   Articles in date range: {articles_in_range}")
    print(f"   Articles outside range: {articles_outside_range}")
    print("\n" + "=" * 50)
    print(" ENHANCED SCRAPING SUMMARY")
    print("=" * 50)
    print(f" Articles in date range: {articles_in_range}")
    print(f" Articles outside range: {articles_outside_range}")
    print(f" Total articles to save: {len(filtered_articles)}")

    if filtered_articles:
        # Count articles with images
        articles_with_images = sum(1 for article in filtered_articles if article.get('image_url'))
        print(f" Articles with images: {articles_with_images}/{len(filtered_articles)} ({articles_with_images/len(filtered_articles)*100:.1f}%)")

        # Show sample titles
        print(f"\n Sample articles:")
        for i, article in enumerate(filtered_articles[:3], 1):
            title = article.get('title', 'Unknown')[:80]
            print(f"   {i}. {title}...")

    # Save to JSON file in the data directory (even if empty)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    data_dir = os.path.join(project_root, "data")
    os.makedirs(data_dir, exist_ok=True)
    json_filename = os.path.join(data_dir, "themorning_latest_news.json")

    try:
        if not filtered_articles:
            print(f" [INFO] 0 articles scraped. Preserving existing data in {json_filename} intact.")
        else:
            with open(json_filename, 'w', encoding='utf-8') as jsonfile:
                json.dump(filtered_articles, jsonfile, ensure_ascii=False, indent=2)

        if not filtered_articles:
            print(f" [WARNING] No articles found in the specified date range ({start_date} to {end_date})")
            print(f" [INFO] Saved empty JSON array to {json_filename}")
            print(" [INFO] Try expanding your date range or check if The Morning has articles for those dates")
        else:
            print(f"\n Successfully saved {len(filtered_articles)} articles to {json_filename}")
            print(f" File location: {os.path.abspath(json_filename)}")
            print(f" Date range: {start_date} to {end_date}")

    except Exception as e:
        print(f" Error saving to file: {e}")

    print("\n The Morning Enhanced Scraper completed successfully!")

if __name__ == "__main__":
    _scraper_dir = os.path.dirname(os.path.abspath(__file__))
    if _scraper_dir not in sys.path:
        sys.path.insert(0, _scraper_dir)
    from incremental import is_incremental_mode

    if is_incremental_mode():
        from incremental_outlets import run_incremental_for_module

        run_incremental_for_module("themorning_selenium_json")
        sys.exit(0)

    # Check if date range is provided as command line arguments
    if len(sys.argv) >= 3:
        try:
            start_date = datetime.strptime(sys.argv[1], "%Y-%m-%d").date()
            end_date = datetime.strptime(sys.argv[2], "%Y-%m-%d").date()
            main(start_date, end_date)
        except ValueError as e:
            print(f" Invalid date format. Use YYYY-MM-DD. Error: {e}")
            print(" Example: python themorning_selenium_json.py 2025-01-18 2025-01-19")
    else:
        main()  # Use default date range 