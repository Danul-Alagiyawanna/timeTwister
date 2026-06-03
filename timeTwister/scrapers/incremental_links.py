"""Link-only collectors for incremental scrapes (first list page per category URL)."""
from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import urljoin

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
    page_url = base + "/"
    _navigate(driver, page_url, 4)
    links: list[str] = []
    seen: set[str] = set()
    for i in range(1, 101):
        xpath = f"/html/body/div[1]/div[3]/div[1]/div/ul/li[{i}]/article/div[2]/div[1]/h2/a"
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


def collect_dailymirror_links(driver: Any, url: str) -> list[str]:
    _navigate(driver, url, 5)
    try:
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located(
                (By.XPATH, "/html/body/div[7]/div/div/div/div[1]/div[2]")
            )
        )
    except Exception:
        return []
    links: list[str] = []
    for i in range(1, 31):
        heading_xpath = f"/html/body/div[7]/div/div/div/div[1]/div[2]/div[{i}]/div/div[1]/a[2]/h3"
        try:
            heading = driver.find_element(By.XPATH, heading_xpath)
            parent = heading.find_element(By.XPATH, "..")
            href = parent.get_attribute("href")
            if href:
                links.append(href)
        except Exception:
            break
    return links


def _economynext_is_blocked(driver: Any) -> bool:
    """Return True when Cloudflare is serving a challenge instead of real content."""
    try:
        title = driver.title or ""
        if "just a moment" in title.lower() or "checking your browser" in title.lower():
            print(f"[WARN] EconomyNext Cloudflare block detected (title: {title!r})")
            return True
        # Also check if there are basically no economynext.com article links at all
        source_snippet = (driver.page_source or "")[:500]
        if "challenge" in source_snippet.lower() and "economynext" not in source_snippet.lower():
            print("[WARN] EconomyNext: Cloudflare challenge page detected in source")
            return True
    except Exception:
        pass
    return False


def collect_economynext_homepage_links(driver: Any, _url: str) -> list[str]:
    _navigate(driver, "https://economynext.com/", 5)

    if _economynext_is_blocked(driver):
        print("[WARN] EconomyNext homepage blocked by Cloudflare on this IP — returning []")
        return []

    soup = BeautifulSoup(driver.page_source, "html.parser")
    links_with_ids: list[tuple[str, int]] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("/"):
            href = "https://economynext.com" + href
        if not href.startswith("https://economynext.com"):
            continue
        match = re.search(r"-(\d+)/?$", href)
        if match:
            pid = int(match.group(1))
            if (href, pid) not in links_with_ids:
                links_with_ids.append((href, pid))
    if not links_with_ids:
        print(f"[WARN] EconomyNext: 0 article links found (page title: {driver.title!r})")
        return []
    threshold = max(pid for _, pid in links_with_ids) - 800
    return [u for u, pid in links_with_ids if pid >= threshold]


def collect_economynext_list_links(driver: Any, url: str) -> list[str]:
    _navigate(driver, url, 3)

    if _economynext_is_blocked(driver):
        print("[WARN] EconomyNext more-news blocked by Cloudflare on this IP — returning []")
        return []

    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CLASS_NAME, "story-grid-single-story"))
        )
    except Exception:
        print(f"[WARN] EconomyNext more-news: story-grid-single-story not found (title: {driver.title!r})")
        return []
    links: list[str] = []
    for card in driver.find_elements(By.CLASS_NAME, "story-grid-single-story"):
        try:
            heading = card.find_element(By.CSS_SELECTOR, "h3.recent-top-header a")
        except Exception:
            try:
                heading = card.find_element(By.CSS_SELECTOR, "h3 a")
            except Exception:
                heading = card.find_element(By.TAG_NAME, "a")
        href = heading.get_attribute("href")
        if href:
            links.append(href)
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


def collect_virakesari_links(driver: Any, url: str) -> list[str]:
    sep = "&" if "?" in url else "?"
    page_url = url if "page=" in url else f"{url}{sep}page=1"
    _navigate(driver, page_url, 5)
    soup = BeautifulSoup(driver.page_source, "html.parser")
    links: list[str] = []
    seen: set[str] = set()
    for card in soup.find_all("a", class_="news-item"):
        href = (card.get("href") or "").strip()
        if not href:
            continue
        if not href.startswith("http"):
            href = "https://www.virakesari.lk" + href
        if href not in seen:
            seen.add(href)
            links.append(href)
    return links


def collect_lankadeepa_links(driver: Any, url: str) -> list[str]:
    _navigate(driver, url, 5)
    heading_xpaths = [
        "/html/body/section/div/div[1]/section/div[1]/div[1]/article/a/h2",
        "/html/body/section/div/div[1]/section/div[1]/div[2]/article/a/h2",
    ]
    links: list[str] = []
    for hx in heading_xpaths:
        try:
            el = driver.find_element(By.XPATH, hx)
            parent = el.find_element(By.XPATH, "..")
            href = parent.get_attribute("href")
            if href:
                links.append(href)
        except Exception:
            pass
    for i in range(1, 11):
        hx = f"/html/body/section/div/div[1]/section/div[2]/article[{i}]/div[1]/a/h3"
        try:
            el = driver.find_element(By.XPATH, hx)
            parent = el.find_element(By.XPATH, "..")
            href = parent.get_attribute("href")
            if href:
                links.append(href)
        except Exception:
            continue
    return links


def collect_wp_category_links(driver: Any, url: str, site: str) -> list[str]:
    """Dinamina / Thinakaran style category pages."""
    _navigate(driver, url.rstrip("/") + "/", 3)
    soup = BeautifulSoup(driver.page_source, "html.parser")
    base = f"https://www.{site}.lk"
    links: list[str] = []
    seen: set[str] = set()
    for a in soup.select("ul li article h2 a, section article h2 a, ul section article h2 a"):
        href = a.get("href") or ""
        if href.startswith("/"):
            href = base + href
        if href.startswith("http") and site in href and href not in seen:
            seen.add(href)
            links.append(href)
    return links


def collect_divaina_breaking_links(driver: Any, url: str) -> list[str]:
    _navigate(driver, url, 3)
    links: list[str] = []
    for i in range(1, 51):
        hx = f"/html/body/div/div[2]/div/div/div/div/div/div/div/div/div[{i}]/div/div[2]/h2/a"
        try:
            el = driver.find_element(By.XPATH, hx)
            href = el.get_attribute("href")
            if href:
                links.append(href)
        except Exception:
            if i > 3 and not links:
                break
            continue
    return links


def collect_divaina_main_links(driver: Any, url: str) -> list[str]:
    _navigate(driver, url, 3)
    soup = BeautifulSoup(driver.page_source, "html.parser")
    links: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.startswith("http"):
            href = urljoin("https://www.divaina.lk", href)
        if "divaina.lk" in href and "/article/" in href and href not in seen:
            seen.add(href)
            links.append(href)
    return links[:40]


def collect_thamilan_links(driver: Any, url: str) -> list[str]:
    _navigate(driver, url, 3)
    links: list[str] = []
    seen: set[str] = set()
    for h2 in driver.find_elements(By.CSS_SELECTOR, "h2.hidden.font-heading"):
        try:
            parent_a = h2.find_element(By.XPATH, "./..")
            href = parent_a.get_attribute("href")
            if href and href not in seen:
                seen.add(href)
                links.append(href)
        except Exception:
            continue
    return links
