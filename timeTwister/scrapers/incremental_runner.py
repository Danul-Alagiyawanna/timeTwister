"""
Shared incremental scrape loop (FT.lk-style) for outlet scrapers.

Each scraper wires:
  - data filename
  - list of (name, category_url)
  - collect_links(driver, url) -> ordered list of article URLs
  - fetch_article(driver, url) -> dict for JSON row
  - create_driver() -> WebDriver
"""
from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Any, Callable

from incremental import (
    INCREMENTAL_BOOTSTRAP_LIMIT,
    INCREMENTAL_RUN_LIMIT,
    get_last_scraped_checkpoint,
    is_last_scraped_article,
    load_known_links,
    merge_and_save,
    normalize_link,
    save_replace_only,
)

CollectLinksFn = Callable[[Any, str], list[str]]
FetchArticleFn = Callable[[Any, str], dict[str, Any] | None]
CreateDriverFn = Callable[[], Any]


def data_json_path(filename: str) -> str:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    return os.path.join(project_root, "data", filename)


def default_chrome_options():
    from selenium.webdriver.chrome.options import Options

    opts = Options()
    opts.page_load_strategy = "eager"
    opts.add_argument("--headless")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    return opts


def create_standard_driver(use_undetected: bool = True) -> Any:
    """Headless Chrome; tries undetected_chromedriver when use_undetected=True."""
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager

    chrome_options = default_chrome_options()
    driver = None

    if use_undetected:
        try:
            import undetected_chromedriver as uc

            options = uc.ChromeOptions()
            options.page_load_strategy = "eager"
            options.add_experimental_option(
                "prefs", {"profile.default_content_setting_values": {"popups": 1}}
            )
            driver = uc.Chrome(options=options, use_subprocess=True)
        except Exception:
            driver = None

    if driver is None:
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=chrome_options,
        )

    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    return driver


def article_from_content(metadata: dict[str, Any], link: str) -> dict[str, Any]:
    """Build JSON row from extract_article_content-style dict."""
    article_date = metadata.get("date_published")
    standardized_date = (
        article_date.strftime("%Y-%m-%d %H:%M:%S")
        if article_date
        else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    desc = metadata.get("description") or ""
    return {
        "title": metadata.get("title") or "",
        "link": metadata.get("link") or link,
        "summary": desc,
        "description": desc,
        "date": standardized_date,
        "image_url": metadata.get("image_url") or "",
        "date_source": (
            f"Article page: {standardized_date}"
            if article_date
            else "Incremental scrape"
        ),
    }


def article_from_metadata(metadata: dict[str, Any], link: str) -> dict[str, Any]:
    """Build standard JSON row from extract_article_metadata-style dict."""
    article_date = metadata.get("date_published")
    standardized_date = (
        article_date.strftime("%Y-%m-%d %H:%M:%S")
        if article_date
        else datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    summary = (
        metadata.get("full_content")
        or metadata.get("description")
        or metadata.get("summary")
        or ""
    )
    return {
        "title": metadata.get("title") or "",
        "link": metadata.get("link") or link,
        "summary": summary,
        "description": metadata.get("description") or summary,
        "date": standardized_date,
        "image_url": metadata.get("image_url") or "",
        "date_source": (
            f"Article page: {standardized_date}"
            if article_date
            else "Incremental scrape"
        ),
    }


def run_incremental_scraper(
    *,
    outlet_name: str,
    data_filename: str,
    pages: list[tuple[str, str]],
    collect_links: CollectLinksFn,
    fetch_article: FetchArticleFn,
    create_driver: CreateDriverFn | None = None,
    bootstrap_limit: int | None = None,
    run_limit: int | None = None,
    sleep_between_articles: float = 0.5,
    sleep_after_list_page: float = 2.0,
    use_undetected: bool = True,
    save_mode: str = "replace",
) -> int:
    """
    Scrape list pages top-to-bottom; stop at last checkpoint URL.
    Returns count of new articles saved this run.
    """
    json_path = data_json_path(data_filename)
    checkpoint_link, _ = get_last_scraped_checkpoint(json_path)
    bootstrap = not checkpoint_link
    if bootstrap:
        max_articles = (
            bootstrap_limit
            if bootstrap_limit is not None
            else INCREMENTAL_BOOTSTRAP_LIMIT
        )
    else:
        max_articles = run_limit if run_limit is not None else INCREMENTAL_RUN_LIMIT
    # URLs already saved in the JSON file (replace-only = last run's batch)
    known_previous = load_known_links(json_path)
    if known_previous:
        print(f"[INCREMENTAL] Skipping {len(known_previous)} URL(s) from previous file")

    print(f"[INCREMENTAL] {outlet_name} — stop when last scraped article is detected")
    if bootstrap:
        print(f"[INCREMENTAL] No checkpoint; bootstrap max {max_articles} articles")
    else:
        print(
            f"[INCREMENTAL] Run safety cap: {max_articles} new articles "
            "(if checkpoint not found on feed)"
        )

    driver_factory = create_driver or (lambda: create_standard_driver(use_undetected))
    driver = driver_factory()

    new_articles: list[dict[str, Any]] = []
    seen_this_run: set[str] = set()
    stop_all = False

    try:
        for name, page_url in pages:
            if stop_all:
                break
            print(f"\n{'=' * 50}\n[INCREMENTAL] {outlet_name} / {name}\n{page_url}")

            try:
                links = collect_links(driver, page_url)
            except Exception as e:
                print(f"[ERROR] List page failed ({name}): {e}")
                continue

            print(f"[INFO] {len(links)} links on list page")

            for i, link in enumerate(links, 1):
                norm = normalize_link(link)

                if is_last_scraped_article(link, checkpoint_link):
                    print(f"\n[INCREMENTAL] Reached last scraped article — stopping.")
                    print(f"             {link}")
                    stop_all = True
                    break

                if norm in known_previous:
                    print(f"[SKIP] Already in previous run: {link[:80]}...")
                    continue

                if norm in seen_this_run:
                    continue

                print(f"\n[INFO] New {i}: {link[:80]}...")
                try:
                    row = fetch_article(driver, link)
                    if row:
                        title = (row.get("title") or "").strip()
                        summary = (
                            (row.get("summary") or row.get("description") or "")
                        ).strip()
                        if not title and not summary:
                            print(f"[SKIP] Empty row (no title/body): {link[:80]}...")
                            continue
                        new_articles.append(row)
                        seen_this_run.add(norm)
                except Exception as e:
                    print(f"[ERROR] Article failed: {e}")

                if len(new_articles) >= max_articles:
                    label = "Bootstrap" if bootstrap else "Run safety"
                    print(f"[INCREMENTAL] {label} limit ({max_articles}) reached.")
                    stop_all = True
                    break

                time.sleep(sleep_between_articles)

            if not stop_all and sleep_after_list_page:
                time.sleep(sleep_after_list_page)
    finally:
        driver.quit()

    # Drop empty shells (failed article-page extract on CI)
    new_articles = [
        a
        for a in new_articles
        if (a.get("title") or "").strip() or (a.get("summary") or a.get("description") or "").strip()
    ]

    print(f"\n[INCREMENTAL] New articles this run: {len(new_articles)}")
    if save_mode == "merge":
        merge_and_save(json_path, new_articles)
    else:
        save_replace_only(json_path, new_articles)
    print(f"[INCREMENTAL] {outlet_name} finished.")
    return len(new_articles)
