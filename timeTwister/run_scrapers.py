"""
Script to run all 17 scrapers in sequence.
Order: 1. sundaytimes 2. dailynews 3. ceylontoday 4. dailymirror 5. ftlk 6. economynext 7. morning
       8. island 9. sundayobserver 10. dinamina 11. divaina 12. lankadeepa 13. aruna 
       14. mawbima 15. virakesari 16. thinakaran 17. thamilan
"""
import sys
import os
from datetime import datetime, timedelta

# Add scrapers directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'scrapers'))

import subprocess

from scraper_registry import INCREMENTAL_MODULES as INCREMENTAL_SCRAPERS


def run_scraper(scraper_name, module_name, start_date=None, end_date=None, incremental=False, **kwargs):
    """Run a single scraper as a subprocess and handle errors."""
    print(f"\n{'='*70}")
    print(f"Running scraper: {scraper_name.upper()} (Subprocess)")
    print(f"{'='*70}\n")
    
    # Resolve the path to the scraper script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    scraper_path = os.path.join(script_dir, 'scrapers', f"{module_name}.py")
    
    # Construct the command line arguments
    cmd = [sys.executable, "-u", scraper_path]
    if incremental:
        if module_name not in INCREMENTAL_SCRAPERS:
            print(f"[SKIP] {scraper_name} does not support --incremental yet")
            return True
        cmd.append("--incremental")
    elif start_date and end_date:
        cmd.extend([start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")])
        
    try:
        # Run the scraper script using subprocess.run
        # Use sys.executable to run with the exact same python interpreter
        result = subprocess.run(cmd, check=True)
        print(f"\n[SUCCESS] {scraper_name} completed successfully")
        return True
        
    except subprocess.CalledProcessError as e:
        print(f"\n[ERROR] {scraper_name} failed with exit code: {e.returncode}")
        return False
    except Exception as e:
        print(f"\n[ERROR] {scraper_name} failed to run: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Run all scrapers in sequence."""
    incremental = "--incremental" in sys.argv or os.getenv("SCRAPE_MODE", "").lower() == "incremental"

    # Filter out flags to safely parse dates
    date_args = []
    i = 1
    while i < len(sys.argv):
        if sys.argv[i] in ("--start-from", "--incremental"):
            i += 2 if sys.argv[i] == "--start-from" else 1
        else:
            date_args.append(sys.argv[i])
            i += 1

    if incremental:
        print("\n[MODE] Incremental — scrapers stop when a known article URL is seen")
        start_date = end_date = None
    elif len(date_args) >= 2:
        try:
            start_date = datetime.strptime(date_args[0], "%Y-%m-%d").date()
            end_date = datetime.strptime(date_args[1], "%Y-%m-%d").date()
            print(f"\n[DATE RANGE] Scraping from {start_date} to {end_date}")
        except ValueError as e:
            print(f"[ERROR] Invalid date format. Use YYYY-MM-DD. Error: {e}")
            print("[INFO] Example: python run_scrapers.py 2025-01-18 2025-01-19")
            sys.exit(1)
    else:
        # Default to yesterday and today
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=1)
        print(f"\n[DATE RANGE] No date range provided, using default: {start_date} to {end_date}")
    
    # Define scrapers in order - tuple format: (name, module, kwargs)
    scrapers = [
        ("sundaytimes", "sundaytimes_selenium_json", {}),
        ("dailynews", "dailynews_selenium_json", {}),
        ("ceylontoday", "ceylontoday_selenium_json", {}),
        ("dailymirror", "dailymirror_selenium_json", {}),
        ("ftlk", "ftlk_selenium_json", {"scrape_mode": "all"}),
        ("economynext", "economynext_selenium_json", {}),
        ("morning", "themorning_selenium_json", {}),
        ("island", "island_selenium_json", {}),
        ("sundayobserver", "sundayobserver_selenium_json", {}),
        ("dinamina", "dinamina_selenium_json", {}),
        ("divaina", "divaina_selenium_json", {}),
        ("lankadeepa", "lankadeepa_selenium_json", {}),
        ("aruna", "aruna_selenium_json", {}),
        ("mawbima", "mawbima_selenium_json", {}),
        ("virakesari", "virakesari_selenium_json", {}),
        ("thinakaran", "thinakaran_selenium_json", {}),
        ("thamilan", "thamilan_selenium_json", {}),
    ]
    
    # Determine if we need to resume from a specific scraper
    start_from_index = 0
    if "--start-from" in sys.argv:
        try:
            idx = sys.argv.index("--start-from")
            start_name = sys.argv[idx + 1].lower()
            for i, (s_name, _, _) in enumerate(scrapers):
                if s_name.lower() == start_name:
                    start_from_index = i
                    break
            print(f"\n[INFO] Resuming execution from: {start_name.upper()}")
        except IndexError:
            pass
            
    print(f"\n{'='*70}")
    print("STARTING SCRAPER RUN")
    print(f"{'='*70}")
    print(f"Total scrapers: {len(scrapers)}")
    print(f"Order: {', '.join([s[0] for s in scrapers])}")
    
    results = {}
    
    # Run each scraper sequentially
    for i, (scraper_name, module_name, kwargs) in enumerate(scrapers[start_from_index:], start_from_index + 1):
        print(f"\n[{i}/{len(scrapers)}] Starting {scraper_name}...")
        
        success = run_scraper(
            scraper_name, module_name, start_date, end_date, incremental=incremental, **kwargs
        )
        results[scraper_name] = success
        
        # Small delay between scrapers
        if i < len(scrapers):
            print("\n[INFO] Waiting 2 seconds before next scraper...")
            import time
            time.sleep(2)
    
    # Print summary
    print(f"\n{'='*70}")
    print("SCRAPER RUN SUMMARY")
    print(f"{'='*70}")
    
    successful = [name for name, success in results.items() if success]
    failed = [name for name, success in results.items() if not success]
    
    print(f"\nSuccessful: {len(successful)}/{len(scrapers)}")
    for name in successful:
        print(f"  ✓ {name}")
    
    if failed:
        print(f"\nFailed: {len(failed)}/{len(scrapers)}")
        for name in failed:
            print(f"  ✗ {name}")
    
    print(f"\n{'='*70}")
    
    if failed:
        sys.exit(1)
    else:
        print("[SUCCESS] All scrapers completed successfully!")

if __name__ == "__main__":
    main()
