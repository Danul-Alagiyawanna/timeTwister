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
    INCREMENTAL_BOOTSTRAP_LIMIT_PER_SECTION,
    INCREMENTAL_RUN_LIMIT,
    INCREMENTAL_RUN_LIMIT_PER_SECTION,
    get_section_checkpoint,
    load_incremental_boundary_links,
    load_known_links,
    merge_and_save,
    apply_section_head_checkpoints,
    migrate_global_checkpoint_to_sections,
    normalize_link,
    save_replace_only,
    should_stop_at_feed_item,
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
    """Headless Chrome; tries undetected_chromedriver when use_undetected=True.

    Retries with the detected Chrome version when the chromedriver version
    mismatch error fires (common on GHA where Chrome auto-updates).
    Falls back to plain Selenium only when UC is unavailable or fails entirely.
    """
    import re as _re

    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager

    chrome_options = default_chrome_options()
    driver = None

    if use_undetected:
        try:
            import undetected_chromedriver as uc

            uc_options = uc.ChromeOptions()
            uc_options.page_load_strategy = "eager"
            uc_options.add_experimental_option(
                "prefs", {"profile.default_content_setting_values": {"popups": 1}}
            )
            try:
                driver = uc.Chrome(options=uc_options, use_subprocess=True)
            except Exception as first_err:
                # Detect Chrome version from error message and retry
                err_msg = str(first_err)
                # Use installed Chrome version only — not chromedriver's "supports version N"
                match = _re.search(r"Current browser version is (\d+)", err_msg)
                if match:
                    major = int(match.group(1))
                    print(f"[INFO] UC version mismatch; retrying with version_main={major}")
                    try:
                        retry_opts = uc.ChromeOptions()
                        retry_opts.page_load_strategy = "eager"
                        retry_opts.add_experimental_option(
                            "prefs", {"profile.default_content_setting_values": {"popups": 1}}
                        )
                        driver = uc.Chrome(
                            options=retry_opts,
                            use_subprocess=True,
                            version_main=major,
                        )
                    except Exception:
                        driver = None
                else:
                    driver = None
        except ImportError:
            driver = None

    if driver is None:
        print("[INFO] Falling back to plain Selenium ChromeDriver")
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


def _finalize_incremental_save(
    json_path: str,
    new_articles: list[dict[str, Any]],
    *,
    outlet_name: str,
    save_mode: str,
    before_save: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None,
) -> int:
    new_articles = [
        a
        for a in new_articles
        if (a.get("title") or "").strip()
        or (a.get("summary") or a.get("description") or "").strip()
    ]
    print(f"\n[INCREMENTAL] New articles this run: {len(new_articles)}")
    if before_save and new_articles:
        new_articles = before_save(new_articles)
    if save_mode == "merge":
        merge_and_save(json_path, new_articles)
    elif new_articles:
        save_replace_only(json_path, new_articles)
    else:
        print("[INCREMENTAL] No new articles — keeping existing data file unchanged")
    print(f"[INCREMENTAL] {outlet_name} finished.")
    return len(new_articles)


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
    per_section_limit: bool | None = None,
    sleep_between_articles: float = 0.5,
    sleep_after_list_page: float = 2.0,
    use_undetected: bool = True,
    save_mode: str = "replace",
    before_save: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None = None,
) -> int:
    """
    Scrape list pages top-to-bottom; stop at last checkpoint URL.
    Multi-page outlets use per-section checkpoints (each category independently).
    Returns count of new articles saved this run.
    """
    json_path = data_json_path(data_filename)
    per_section = per_section_limit if per_section_limit is not None else len(pages) > 1

    if per_section and len(pages) > 1:
        return _run_incremental_scraper_per_section(
            outlet_name=outlet_name,
            json_path=json_path,
            pages=pages,
            collect_links=collect_links,
            fetch_article=fetch_article,
            create_driver=create_driver,
            bootstrap_limit=bootstrap_limit,
            run_limit=run_limit,
            sleep_between_articles=sleep_between_articles,
            sleep_after_list_page=sleep_after_list_page,
            use_undetected=use_undetected,
            save_mode=save_mode,
            before_save=before_save,
        )

    checkpoint_link, known_previous = load_incremental_boundary_links(json_path)
    bootstrap = not checkpoint_link
    if bootstrap:
        max_articles = (
            bootstrap_limit
            if bootstrap_limit is not None
            else (
                INCREMENTAL_BOOTSTRAP_LIMIT_PER_SECTION
                if per_section
                else INCREMENTAL_BOOTSTRAP_LIMIT
            )
        )
    else:
        if run_limit is not None:
            max_articles = run_limit
        elif per_section:
            max_articles = INCREMENTAL_RUN_LIMIT_PER_SECTION
        else:
            max_articles = INCREMENTAL_RUN_LIMIT
    if known_previous:
        print(
            f"[INCREMENTAL] Boundary set: {len(known_previous)} URL(s) "
            "(archive + checkpoint)"
        )

    print(f"[INCREMENTAL] {outlet_name} — stop when last scraped article is detected")
    cap_label = "per page/section" if per_section else "total"
    if bootstrap:
        print(
            f"[INCREMENTAL] No checkpoint; bootstrap max {max_articles} "
            f"articles {cap_label}"
        )
    else:
        print(
            f"[INCREMENTAL] Run safety cap: {max_articles} new articles {cap_label} "
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
            page_new = 0
            print(f"\n{'=' * 50}\n[INCREMENTAL] {outlet_name} / {name}\n{page_url}")

            try:
                links = collect_links(driver, page_url)
            except Exception as e:
                print(f"[ERROR] List page failed ({name}): {e}")
                continue

            print(f"[INFO] {len(links)} links on list page")

            for i, link in enumerate(links, 1):
                norm = normalize_link(link)

                stop_reason = should_stop_at_feed_item(
                    link,
                    checkpoint_link=checkpoint_link,
                    known_previous=known_previous,
                )
                if stop_reason == "checkpoint":
                    print(
                        f"\n[INCREMENTAL] Reached boundary ({stop_reason}) — stopping."
                    )
                    print(f"             {link}")
                    stop_all = True
                    break
                if stop_reason == "known_previous":
                    print(
                        f"\n[INCREMENTAL] Reached boundary ({stop_reason}) on this page."
                    )
                    print(f"             {link}")
                    break

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
                        page_new += 1
                except Exception as e:
                    print(f"[ERROR] Article failed: {e}")

                if page_new >= max_articles:
                    label = "Bootstrap" if bootstrap else "Run safety"
                    print(
                        f"[INCREMENTAL] {label} limit ({max_articles}) "
                        f"for {name}"
                        + (" — next page" if per_section else " — stopping.")
                    )
                    if not per_section:
                        stop_all = True
                    break

                time.sleep(sleep_between_articles)

            if not stop_all and sleep_after_list_page:
                time.sleep(sleep_after_list_page)
    finally:
        driver.quit()

    return _finalize_incremental_save(
        json_path,
        new_articles,
        outlet_name=outlet_name,
        save_mode=save_mode,
        before_save=before_save,
    )


def _run_incremental_scraper_per_section(
    *,
    outlet_name: str,
    json_path: str,
    pages: list[tuple[str, str]],
    collect_links: CollectLinksFn,
    fetch_article: FetchArticleFn,
    create_driver: CreateDriverFn | None,
    bootstrap_limit: int | None,
    run_limit: int | None,
    sleep_between_articles: float,
    sleep_after_list_page: float,
    use_undetected: bool,
    save_mode: str,
    before_save: Callable[[list[dict[str, Any]]], list[dict[str, Any]]] | None,
) -> int:
    section_keys = [name for name, _ in pages]
    migrate_global_checkpoint_to_sections(json_path, section_keys)
    bootstrap = not any(get_section_checkpoint(json_path, k)[0] for k in section_keys)
    if bootstrap:
        max_articles = (
            bootstrap_limit
            if bootstrap_limit is not None
            else INCREMENTAL_BOOTSTRAP_LIMIT_PER_SECTION
        )
    else:
        max_articles = (
            run_limit if run_limit is not None else INCREMENTAL_RUN_LIMIT_PER_SECTION
        )

    known_previous = load_known_links(json_path)
    if known_previous:
        print(
            f"[INCREMENTAL] Skipping {len(known_previous)} URL(s) from previous file"
        )

    print(f"[INCREMENTAL] {outlet_name} — per-section checkpoints")
    if bootstrap:
        print(f"[INCREMENTAL] No section checkpoint; bootstrap max {max_articles} per section")
    else:
        print(
            f"[INCREMENTAL] Run safety cap: {max_articles} new articles per section "
            "(if section checkpoint not found on feed)"
        )

    driver_factory = create_driver or (lambda: create_standard_driver(use_undetected))
    driver = driver_factory()
    new_articles: list[dict[str, Any]] = []
    seen_this_run: set[str] = set()
    section_links: dict[str, list[str]] = {}

    try:
        print("\n[INCREMENTAL] Phase 1 — list pages")
        for name, page_url in pages:
            print(f"\n{'=' * 50}\n[INCREMENTAL] {outlet_name} / {name}\n{page_url}")
            try:
                links = collect_links(driver, page_url)
                section_links[name] = links
                print(f"[INFO] {len(links)} links on list page")
            except Exception as e:
                print(f"[ERROR] List page failed ({name}): {e}")
                section_links[name] = []
            if sleep_after_list_page:
                time.sleep(sleep_after_list_page)

        print("\n[INCREMENTAL] Phase 2 — fetch new articles per section")
        for name, _page_url in pages:
            links = section_links.get(name, [])
            sec_ckpt, _ = get_section_checkpoint(json_path, name)
            print(
                f"\n[PHASE 2] {name} — checkpoint: "
                f"{(sec_ckpt or 'None')[:70]}"
            )
            page_new = 0

            for i, link in enumerate(links, 1):
                norm = normalize_link(link)
                if sec_ckpt and norm == normalize_link(sec_ckpt):
                    print("  [STOP] Reached section checkpoint")
                    break
                if norm in known_previous:
                    print(f"  [STOP] Already in previous run: {link[:70]}")
                    break
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
                        page_new += 1
                except Exception as e:
                    print(f"[ERROR] Article failed: {e}")

                if page_new >= max_articles:
                    label = "Bootstrap" if bootstrap else "Run safety"
                    print(
                        f"[INCREMENTAL] {label} limit ({max_articles}) "
                        f"for {name} — next section"
                    )
                    break

                time.sleep(sleep_between_articles)

        apply_section_head_checkpoints(
            json_path,
            section_links,
            new_articles,
            section_keys=section_keys,
        )
    finally:
        driver.quit()

    return _finalize_incremental_save(
        json_path,
        new_articles,
        outlet_name=outlet_name,
        save_mode=save_mode,
        before_save=before_save,
    )
