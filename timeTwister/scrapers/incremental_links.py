"""
Link-only collectors for incremental list pages (one function per outlet/layout).

These are intentionally outlet-specific: each site uses different DOM (XPath, CSS,
pagination). incremental_outlets.py wires each scraper to its collector + fetch fn;
stop/checkpoint logic lives in incremental.py / incremental_runner.py / per-outlet RSS.

To add an outlet: implement collect_<outlet>_links(driver, category_url) here, then
register pages in incremental_outlets.run_<outlet>_incremental() or the scraper's
main_incremental().
"""
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
    """Delegate to scraper (newest-first ordering + list hints)."""
    from dailymirror_selenium_json import collect_list_page_links

    return collect_list_page_links(driver, url)


def _economynext_fetch_with_cffi(url: str) -> "BeautifulSoup | None":
    """Fetch EconomyNext page using curl_cffi (Chrome TLS fingerprint → bypasses Cloudflare)."""
    try:
        from curl_cffi import requests as cf_requests  # type: ignore

        resp = cf_requests.get(
            url,
            impersonate="chrome",
            timeout=30,
            headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        if resp.status_code in (200, 202):
            title_match = re.search(r"<title[^>]*>(.*?)</title>", resp.text, re.IGNORECASE | re.DOTALL)
            title = (title_match.group(1) if title_match else "").strip()
            if "just a moment" in title.lower():
                print(f"[WARN] curl_cffi also got Cloudflare challenge for {url}")
                return None
            print(f"[INFO] curl_cffi fetched {url} OK (status 200, title: {title[:60]!r})")
            return BeautifulSoup(resp.text, "html.parser")
        print(f"[WARN] curl_cffi got status {resp.status_code} for {url}")
        return None
    except ImportError:
        print("[WARN] curl_cffi not installed — cannot bypass Cloudflare from this IP")
        return None
    except Exception as e:
        print(f"[WARN] curl_cffi failed for {url}: {e}")
        return None


def _economynext_is_blocked(driver: Any) -> bool:
    """Return True when Cloudflare is serving a challenge instead of real content."""
    try:
        title = driver.title or ""
        if "just a moment" in title.lower() or "checking your browser" in title.lower():
            print(f"[WARN] EconomyNext Cloudflare block via Selenium (title: {title!r})")
            return True
        source_snippet = (driver.page_source or "")[:600]
        if "challenge" in source_snippet.lower() and "economynext" not in source_snippet.lower():
            print("[WARN] EconomyNext: Cloudflare challenge page in source")
            return True
        # No article links at all → also blocked
        if not re.search(r"economynext\.com/[^\"']+?-\d+", source_snippet):
            all_links = re.findall(r'href=["\']([^"\']+)["\']', source_snippet)
            if len(all_links) < 5:
                print(f"[WARN] EconomyNext: suspiciously few links ({len(all_links)}) — likely blocked")
                return True
    except Exception:
        pass
    return False


def _parse_economynext_article_links(soup: "BeautifulSoup") -> list[str]:
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
        return []
    threshold = max(pid for _, pid in links_with_ids) - 800
    return [u for u, pid in links_with_ids if pid >= threshold]


def collect_economynext_homepage_links(driver: Any, _url: str) -> list[str]:
    url = "https://economynext.com/"
    _navigate(driver, url, 5)

    if _economynext_is_blocked(driver):
        print("[INFO] EconomyNext: Selenium blocked — trying curl_cffi fallback...")
        soup = _economynext_fetch_with_cffi(url)
        if soup is None:
            return []
        links = _parse_economynext_article_links(soup)
        print(f"[INFO] curl_cffi found {len(links)} article links on homepage")
        return links

    soup = BeautifulSoup(driver.page_source, "html.parser")
    links = _parse_economynext_article_links(soup)
    if not links:
        print(f"[WARN] EconomyNext: 0 article links via Selenium (title: {driver.title!r})")
        print("[INFO] Trying curl_cffi fallback...")
        soup2 = _economynext_fetch_with_cffi(url)
        if soup2:
            links = _parse_economynext_article_links(soup2)
            print(f"[INFO] curl_cffi found {len(links)} article links")
    return links


def collect_economynext_list_links(driver: Any, url: str) -> list[str]:
    _navigate(driver, url, 3)

    if _economynext_is_blocked(driver):
        print("[INFO] EconomyNext more-news: Selenium blocked — trying curl_cffi fallback...")
        soup = _economynext_fetch_with_cffi(url)
        if soup is None:
            return []
        # more-news page: cards have class story-grid-single-story
        links: list[str] = []
        for card in soup.find_all(class_="story-grid-single-story"):
            a = card.find("a", href=True)
            if a:
                href = a["href"]
                if href.startswith("/"):
                    href = "https://economynext.com" + href
                links.append(href)
        if not links:
            # fallback: any economynext article link
            links = _parse_economynext_article_links(soup)
        print(f"[INFO] curl_cffi found {len(links)} links on more-news")
        return links

    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CLASS_NAME, "story-grid-single-story"))
        )
    except Exception:
        print(f"[WARN] EconomyNext more-news: story-grid-single-story not found (title: {driver.title!r})")
        return []
    links = []
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


_VIRAKESARI_ARTICLE_ID_RE = re.compile(r"/article/(\d+)")


def virakesari_article_id(link: str) -> int:
    m = _VIRAKESARI_ARTICLE_ID_RE.search(link or "")
    return int(m.group(1)) if m else 0


_VIRAKESARI_MAIN_SELECTORS = (
    "section.group-news div.article-landing a.news-item",
    "section.group-news div.three-grid a.news-item",
)


def _virakesari_page_ready(driver: Any) -> bool:
    src = (driver.page_source or "").lower()
    title = (driver.title or "").lower()
    if "just a moment" in title or "checking your browser" in src:
        return False
    return "/article/" in src or "news-item" in src


def _collect_virakesari_hrefs(driver: Any, *, main_grid_only: bool) -> list[str]:
    selectors = list(_VIRAKESARI_MAIN_SELECTORS) if main_grid_only else ["a.news-item"]
    hrefs: list[str] = []
    for selector in selectors:
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, selector):
                href = (el.get_attribute("href") or "").strip()
                if href:
                    hrefs.append(href)
        except Exception:
            continue
    if not hrefs and main_grid_only:
        return _collect_virakesari_hrefs(driver, main_grid_only=False)
    return hrefs


def collect_virakesari_links(driver: Any, url: str) -> list[str]:
    """Main category grid only — excludes sidebar promos from other sections."""
    import os

    is_ci = os.getenv("CI", "").lower() in ("1", "true", "yes")
    sep = "&" if "?" in url else "?"
    page_url = url if "page=" in url else f"{url}{sep}page=1"
    settle = 8 if is_ci else 5
    wait_timeout = 45 if is_ci else 25

    raw_hrefs: list[str] = []
    for attempt in range(1, 4):
        try:
            driver.get(page_url)
        except Exception as e:
            print(f"[WARN] Virakesari driver.get attempt {attempt}/3: {e}")
        try:
            WebDriverWait(driver, wait_timeout).until(
                lambda d: _virakesari_page_ready(d)
            )
        except Exception:
            pass
        try:
            driver.execute_script(
                "document.querySelectorAll('.modal.show,#randomPopup').forEach("
                "el => el.remove());"
                "document.body.classList.remove('modal-open');"
            )
        except Exception:
            pass
        time.sleep(settle)
        raw_hrefs = _collect_virakesari_hrefs(driver, main_grid_only=True)
        if raw_hrefs:
            break
        print(
            f"[WARN] Virakesari list attempt {attempt}/3 — "
            f"title={driver.title!r} src={len(driver.page_source or '')} bytes"
        )
        if attempt < 3:
            time.sleep(6)

    links: list[str] = []
    seen_ids: set[int] = set()
    for href in raw_hrefs:
        if not href.startswith("http"):
            href = "https://www.virakesari.lk" + href
        aid = virakesari_article_id(href)
        if not aid or aid in seen_ids:
            continue
        seen_ids.add(aid)
        links.append(href.split("?")[0])

    if not links:
        soup = BeautifulSoup(driver.page_source or "", "html.parser")
        cards: list = []
        for selector in _VIRAKESARI_MAIN_SELECTORS:
            cards.extend(soup.select(selector))
        if not cards:
            cards = soup.find_all("a", class_="news-item")
            if cards:
                print("[WARN] Virakesari DOM fallback: all news-item links")
        for card in cards:
            href = (card.get("href") or "").strip()
            if not href:
                continue
            if not href.startswith("http"):
                href = "https://www.virakesari.lk" + href
            aid = virakesari_article_id(href)
            if not aid or aid in seen_ids:
                continue
            seen_ids.add(aid)
            links.append(href.split("?")[0])

    links.sort(key=virakesari_article_id, reverse=True)
    if links:
        ids = [virakesari_article_id(u) for u in links[:3]]
        print(
            f"[INFO] Virakesari main-grid ids (newest-first): "
            f"{ids[0]}..{ids[-1]} ({len(links)} links)"
        )
    else:
        print(
            f"[ERROR] Virakesari list empty — title={driver.title!r} "
            f"src={len(driver.page_source or '')} bytes"
        )
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
        if (
            "divaina.lk" in href
            and (
                "/main-news/" in href
                or "/provincial-news/" in href
                or "/article/" in href
            )
            and href not in seen
        ):
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
