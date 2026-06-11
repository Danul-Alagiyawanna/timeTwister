"""
Thinakaran fetch + parse via Scrapling (https://github.com/D4Vinci/Scrapling).
RSS + HTML list/article fetch with curl_cffi GHA fallback.
"""
from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any

from bs4 import BeautifulSoup

from scrapling_fetch import (
    _is_ci,
    fetch_bytes,
    fetch_text,
    is_cloudflare_block,
)

_STEALTH_KW = {
    "stealth_fallback": True,
    "stealth_first": _is_ci(),
}
_LIST_WAIT = "ul.penci-wrapper-data"

BASE_URL = "https://www.thinakaran.lk"
_HEADERS = {"Referer": f"{BASE_URL}/"}

THINAKARAN_SECTIONS = (
    "local",
    "politics",
    "features",
    "editorial",
    "sports",
    "business",
)

_ARTICLE_RE = re.compile(
    r"https?://(?:www\.)?thinakaran\.lk/\d{4}/\d{2}/\d{2}/",
    re.I,
)


def is_article_url(link: str) -> bool:
    return bool(link and _ARTICLE_RE.search(link))


def html_ready(html: str) -> bool:
    if not html or len(html) < 500:
        return False
    lower = html.lower()
    if is_cloudflare_block(html):
        return False
    return "penci-wrapper-data" in lower or "penci-entry-title" in lower


def parse_thinakaran_date(date_text: str) -> datetime | None:
    try:
        return datetime.strptime(date_text.strip(), "%B %d, %Y")
    except Exception:
        pass
    for fmt in ("%d %B %Y", "%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(date_text.strip(), fmt)
        except Exception:
            continue
    return None


def _html_to_plain(html: str) -> str:
    if not html:
        return ""
    try:
        return BeautifulSoup(html, "html.parser").get_text(separator="\n", strip=True)
    except Exception:
        return html.strip()


def parse_list_links_from_html(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one("ul.penci-wrapper-data")
    if not container:
        return []
    links: list[str] = []
    seen: set[str] = set()
    for article in container.find_all("article"):
        a = article.select_one("h2.penci-entry-title a")
        if not a:
            continue
        href = (a.get("href") or "").strip()
        if not href or href in seen:
            continue
        seen.add(href)
        links.append(href.split("?")[0])
    return links


def parse_list_cards_from_html(html: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    container = soup.select_one("ul.penci-wrapper-data")
    if not container:
        return []
    cards: list[dict[str, Any]] = []
    for article in container.find_all("article"):
        a = article.select_one("h2.penci-entry-title a")
        if not a:
            continue
        title = a.get_text(strip=True)
        link = (a.get("href") or "").strip().split("?")[0]
        article_date = None
        date_text = ""
        time_el = article.find("time", class_="entry-date")
        if time_el and time_el.get("datetime"):
            try:
                date_str = time_el["datetime"].split("+")[0].split("T")[0]
                article_date = datetime.strptime(date_str, "%Y-%m-%d")
                date_text = time_el.get_text(strip=True)
            except Exception:
                pass
        if not article_date and time_el:
            date_text = time_el.get_text(strip=True)
            article_date = parse_thinakaran_date(date_text)
        cards.append(
            {
                "title": title,
                "link": link,
                "date_text": date_text,
                "date_published": article_date,
            }
        )
    return cards


def fetch_category_html(url: str, *, timeout: int | None = None) -> str | None:
    if timeout is None:
        timeout = 60 if _is_ci() else 25
    page_url = url.rstrip("/") + "/"
    return fetch_text(
        page_url,
        timeout=timeout,
        extra_headers=_HEADERS,
        wait_selector=_LIST_WAIT,
        **_STEALTH_KW,
    )


def collect_category_links(url: str, *, max_attempts: int | None = None) -> list[str]:
    if max_attempts is None:
        max_attempts = 1 if _is_ci() else 3
    for attempt in range(1, max_attempts + 1):
        html = fetch_category_html(url)
        if not html or not html_ready(html):
            print(
                f"[WARN] Thinakaran Scrapling attempt {attempt}/{max_attempts} — "
                f"no ready HTML ({len(html or '')} bytes)"
            )
            if attempt < max_attempts:
                time.sleep(6)
            continue
        links = parse_list_links_from_html(html)
        if links:
            print(f"[INFO] Thinakaran list (Scrapling): {len(links)} link(s) from {url}")
            return links
        print(f"[WARN] Thinakaran Scrapling attempt {attempt}/{max_attempts} — parsed 0 links")
        if attempt < max_attempts:
            time.sleep(6)
    return []


def extract_article_content_from_html(html: str, url: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")

    date_published = None
    date_meta = soup.find("meta", attrs={"property": "article:published_time"})
    if date_meta and date_meta.has_attr("content"):
        try:
            date_str = date_meta["content"]
            parts = date_str.split("+")[0].split("T")
            if len(parts) == 2:
                date_published = datetime.strptime(
                    f"{parts[0]} {parts[1]}", "%Y-%m-%d %H:%M:%S"
                )
        except ValueError:
            pass

    if not date_published:
        time_tag = soup.find("time")
        if time_tag and time_tag.has_attr("datetime"):
            try:
                parts = time_tag["datetime"].split("+")[0].split("T")
                if len(parts) == 2:
                    date_published = datetime.strptime(
                        f"{parts[0]} {parts[1]}", "%Y-%m-%d %H:%M:%S"
                    )
            except Exception:
                pass

    image_url = ""
    og_image = soup.find("meta", attrs={"property": "og:image"})
    if og_image and og_image.has_attr("content"):
        image_url = og_image["content"]

    full_article_text = ""
    for selector in (
        "article .entry-content",
        ".entry-content",
        "article .post-content",
        ".post-content",
        ".article-content",
        "article",
    ):
        article_div = soup.select_one(selector)
        if not article_div:
            continue
        paragraph_texts = [
            p.get_text(strip=True)
            for p in article_div.find_all("p")
            if p.get_text(strip=True)
        ]
        if not paragraph_texts:
            all_text = article_div.get_text(separator="\n", strip=True)
            paragraph_texts = [
                c for c in (x.strip() for x in all_text.split("\n")) if c and len(c) > 20
            ]
        if paragraph_texts:
            full_article_text = "\n\n".join(paragraph_texts)
            print(
                f"     [OK] Extracted {len(paragraph_texts)} paragraphs "
                f"({len(full_article_text)} chars)"
            )
            break

    title = ""
    og_title = soup.find("meta", attrs={"property": "og:title"})
    if og_title and og_title.has_attr("content"):
        title = og_title["content"]

    return {
        "date_published": date_published,
        "image_url": image_url,
        "description": full_article_text,
        "title": title,
        "link": url,
    }


def fetch_article_content(url: str, *, timeout: int = 30) -> dict[str, Any] | None:
    if _is_ci():
        timeout = max(timeout, 60)
    html = fetch_text(
        url,
        timeout=timeout,
        extra_headers=_HEADERS,
        wait_selector="article .entry-content, .entry-content, article",
        **_STEALTH_KW,
    )
    if not html or is_cloudflare_block(html):
        return None
    return extract_article_content_from_html(html, url)


def parse_rss(xml_text: str) -> list[dict[str, Any]]:
    ns = {
        "content": "http://purl.org/rss/1.0/modules/content/",
        "dc": "http://purl.org/dc/elements/1.1/",
        "media": "http://search.yahoo.com/mrss/",
    }
    articles: list[dict[str, Any]] = []
    root = ET.fromstring(xml_text)
    items = root.findall("./channel/item") or root.findall(".//item")
    for item in items:
        link = (item.findtext("link") or "").strip()
        if not is_article_url(link):
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
        if not image_url:
            for html in (content_html, desc_html):
                if not html:
                    continue
                img = BeautifulSoup(html, "html.parser").find("img")
                if img and img.get("src"):
                    image_url = img["src"].strip()
                    break

        articles.append(
            {
                "title": title,
                "link": link.split("?")[0],
                "summary": body_text,
                "description": body_text,
                "date": date_str,
                "image_url": image_url,
                "date_source": f"RSS: {date_str}",
            }
        )
    return articles


def fetch_section_feed(section: str) -> list[dict[str, Any]]:
    """Category RSS via Scrapling (GHA-safe)."""
    feed_url = f"{BASE_URL}/category/{section}/feed/"
    raw = fetch_bytes(
        feed_url,
        accept="application/rss+xml, application/xml, text/xml, */*",
        timeout=60 if _is_ci() else 30,
        expect_xml=True,
        extra_headers={
            **_HEADERS,
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        },
        **_STEALTH_KW,
    )
    if not raw:
        return []
    xml_text = raw.decode("utf-8", errors="replace")
    if is_cloudflare_block(xml_text):
        print(f"[WARN] Cloudflare interstitial on {feed_url}")
        return []
    try:
        items = parse_rss(xml_text)
        if items:
            print(f"[INFO] RSS {section}: {len(items)} article(s)")
        return items
    except Exception as e:
        print(f"[WARN] RSS parse error ({section}): {e}")
        return []


# Back-compat alias for incremental_outlets
fetch_thinakaran_section_feed = fetch_section_feed
