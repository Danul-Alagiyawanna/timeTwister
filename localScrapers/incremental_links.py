"""Link collectors for incremental list pages (Selenium)."""
from __future__ import annotations

import time
from typing import Any

from bs4 import BeautifulSoup
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


def _navigate(driver: Any, url: str, wait_sec: float = 3.0) -> None:
    driver.get(url)
    try:
        WebDriverWait(driver, 30).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
    except Exception:
        pass
    time.sleep(wait_sec)


def collect_dailynews_links(driver: Any, url: str) -> list[str]:
    base = url.rstrip("/")
    _navigate(driver, base + "/", 4)
    links: list[str] = []
    seen: set[str] = set()
    for i in range(1, 101):
        xpath = (
            f"/html/body/div[1]/div[3]/div[1]/div/ul/li[{i}]"
            f"/article/div[2]/div[1]/h2/a"
        )
        try:
            el = driver.find_element(By.XPATH, xpath)
            href = el.get_attribute("href") or ""
            if href.startswith("/"):
                href = "https://dailynews.lk" + href
            if href and href not in seen:
                seen.add(href)
                links.append(href)
        except Exception:
            continue
    return links


def collect_ceylontoday_links(driver: Any, url: str) -> list[str]:
    _navigate(driver, url, 3)
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CLASS_NAME, "tdb_loop"))
        )
    except Exception:
        pass
    soup = BeautifulSoup(driver.page_source, "html.parser")
    links: list[str] = []
    for article in soup.select("div.tdb_module_loop.td_module_wrap"):
        title_tag = article.select_one("h3.entry-title a")
        if title_tag and title_tag.get("href"):
            links.append(title_tag["href"])
    return links


def _scroll_category_feed(driver: Any) -> None:
    try:
        for _ in range(6):
            prev = driver.execute_script("return document.body.scrollHeight")
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1)
            if driver.execute_script("return document.body.scrollHeight") == prev:
                break
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(1)
    except Exception:
        pass


def collect_thinakaran_links(driver: Any, url: str) -> list[str]:
    page_url = url.rstrip("/") + "/"
    _navigate(driver, page_url, 3)
    _scroll_category_feed(driver)

    links: list[str] = []
    seen: set[str] = set()
    try:
        container = driver.find_element(By.CSS_SELECTOR, "ul.penci-wrapper-data")
        for article in container.find_elements(By.TAG_NAME, "article"):
            try:
                a = article.find_element(By.CSS_SELECTOR, "h2.penci-entry-title a")
                href = (a.get_attribute("href") or "").strip()
                if href and href not in seen:
                    seen.add(href)
                    links.append(href.split("?")[0])
            except Exception:
                continue
    except Exception:
        pass
    return links


def collect_dinamina_links(driver: Any, url: str) -> list[str]:
    page_url = url.rstrip("/") + "/"
    _navigate(driver, page_url, 3)
    _scroll_category_feed(driver)

    links: list[str] = []
    seen: set[str] = set()
    max_articles = 50

    for article_idx in range(max_articles):
        if article_idx == 0:
            heading_xpath = (
                "/html/body/div[1]/div[3]/div[1]/div/ul/section/article"
                "/div[2]/div[1]/h2/a"
            )
        elif article_idx <= 4:
            heading_xpath = (
                f"/html/body/div[1]/div[3]/div[1]/div/ul/li[{article_idx}]"
                f"/article/div[2]/h2/a"
            )
        else:
            heading_xpath = (
                f"/html/body/div[1]/div[3]/div[1]/div/ul/li[{article_idx}]"
                f"/article/div[2]/div/h2/a"
            )
        try:
            el = driver.find_element(By.XPATH, heading_xpath)
            href = (el.get_attribute("href") or "").strip()
            if href and href not in seen:
                seen.add(href)
                links.append(href)
        except Exception:
            if article_idx > 5:
                break
            continue

    return links


def collect_module_list_links(driver: Any, url: str, mod: Any) -> list[str]:
    _navigate(driver, url, 3)
    return mod.get_main_article_links(driver)
