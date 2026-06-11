"""
Thinakaran fetch + parse via Scrapling (https://github.com/D4Vinci/Scrapling).
RSS + HTML list/article fetch with curl_cffi GHA fallback.
"""
from __future__ import annotations

import html as html_module
import json
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

_LIST_WAIT = "ul.penci-wrapper-data"
_RSS_ACCEPT = "application/rss+xml, application/xml, text/xml, */*"

BASE_URL = "https://www.thinakaran.lk"
_HEADERS = {"Referer": f"{BASE_URL}/"}
_RSS_HEADERS = {**_HEADERS, "Accept": _RSS_ACCEPT}
_JSON_HEADERS = {**_HEADERS, "Accept": "application/json"}
_WP_POST_FIELDS = "id,title,link,date,excerpt,content,jetpack_featured_media_url"
# Site URL path `features` maps to WP category slug `featured`
_SECTION_WP_SLUG: dict[str, str] = {
    "local": "local",
    "politics": "politics",
    "features": "featured",
    "editorial": "editorial",
    "sports": "sports",
    "business": "business",
}


def _html_stealth_kw() -> dict[str, bool]:
    """Browser stealth only for local HTML fallback — never on CI (Turnstile loop)."""
    if _is_ci():
        return {}
    return {"stealth_fallback": True, "stealth_first": False}

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


_RSS_BOILERPLATE_CUT = re.compile(
    r"(?:𝗙𝗢𝗟𝗟𝗢𝗪 𝗨𝗦 𝗢𝗡|FOLLOW US ON|Thinakaran-WhatsApp-Channel|"
    r"The post .+ appeared first on )",
    re.I | re.S,
)
_RSS_HTML_CUT = re.compile(
    r'<a[^>]+href=["\']https?://(?:www\.)?whatsapp\.com/channel|'
    r'class=["\'][^"\']*pdfemb-viewer|Thinakaran-WhatsApp-Channel',
    re.I,
)
_JUNK_IMG_MARKERS = (
    "thinakaran-whatsapp-channel",
    "whatsapp-channel",
    "pdfemb",
    "favicon",
)


def _trim_rss_html(html: str) -> str:
    if not html:
        return ""
    m = _RSS_HTML_CUT.search(html)
    return html[: m.start()] if m else html


def _strip_rss_boilerplate(text: str) -> str:
    if not text:
        return ""
    parts = _RSS_BOILERPLATE_CUT.split(text, maxsplit=1)
    return parts[0].strip()


def _rss_html_to_plain(html: str) -> str:
    trimmed = _trim_rss_html(html)
    plain = _html_to_plain(trimmed)
    return _strip_rss_boilerplate(plain)


def _first_article_image(html: str) -> str:
    if not html:
        return ""
    trimmed = _trim_rss_html(html)
    try:
        soup = BeautifulSoup(trimmed, "html.parser")
    except Exception:
        return ""
    for img in soup.find_all("img"):
        src = (img.get("src") or img.get("data-src") or "").strip()
        if not src.startswith("http"):
            continue
        lower = src.lower()
        if any(marker in lower for marker in _JUNK_IMG_MARKERS):
            continue
        return src
    return ""


def _normalize_rss_link(link: str) -> str:
    link = (link or "").strip().split("?")[0].split("#")[0]
    return link.rstrip("/") + "/" if link else ""


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


def _fetch_wp_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: int = 30,
) -> Any | None:
    raw = fetch_bytes(
        url,
        params=params,
        accept="application/json",
        timeout=timeout,
        expect_json=True,
        extra_headers=_JSON_HEADERS,
    )
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as e:
        print(f"[WARN] WP-API JSON parse error for {url[:70]}: {e}")
        return None


def _wp_rendered_text(field: Any) -> str:
    if isinstance(field, dict):
        html = field.get("rendered") or ""
    else:
        html = str(field or "")
    if not html:
        return ""
    return html_module.unescape(
        BeautifulSoup(html, "html.parser").get_text(strip=True)
    )


def _wp_post_to_article(post: dict[str, Any]) -> dict[str, Any] | None:
    link = _normalize_rss_link(post.get("link") or "")
    if not is_article_url(link):
        return None

    title = _wp_rendered_text(post.get("title"))
    date_raw = (post.get("date") or "").strip()
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if date_raw:
        try:
            date_str = datetime.fromisoformat(date_raw).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass

    content_html = (post.get("content") or {}).get("rendered", "")
    if not isinstance(content_html, str):
        content_html = ""
    excerpt_html = (post.get("excerpt") or {}).get("rendered", "")
    if not isinstance(excerpt_html, str):
        excerpt_html = ""
    body_text = _rss_html_to_plain(content_html) or _rss_html_to_plain(excerpt_html)

    image_url = (post.get("jetpack_featured_media_url") or "").strip()
    if not image_url:
        image_url = _first_article_image(content_html) or _first_article_image(
            excerpt_html
        )

    return {
        "title": title,
        "link": link,
        "summary": body_text,
        "description": body_text,
        "date": date_str,
        "image_url": image_url,
        "date_source": f"WP-API: {date_str}",
    }


def _wp_category_id(section: str) -> int | None:
    wp_slug = _SECTION_WP_SLUG.get(section, section)
    cats = _fetch_wp_json(
        f"{BASE_URL}/wp-json/wp/v2/categories",
        params={"slug": wp_slug, "_fields": "id,slug"},
    )
    if not isinstance(cats, list) or not cats:
        print(f"[WARN] WP-API category not found for section={section} slug={wp_slug}")
        return None
    cat_id = cats[0].get("id")
    return int(cat_id) if cat_id else None


def fetch_section_wp_api(section: str, *, per_page: int = 15) -> list[dict[str, Any]]:
    """WordPress REST API posts by category — GHA-safe (like economynext)."""
    cat_id = _wp_category_id(section)
    if cat_id is None:
        return []

    posts = _fetch_wp_json(
        f"{BASE_URL}/wp-json/wp/v2/posts",
        params={
            "categories": cat_id,
            "per_page": per_page,
            "_fields": _WP_POST_FIELDS,
            "orderby": "date",
            "order": "desc",
        },
    )
    if not isinstance(posts, list) or not posts:
        print(f"[WARN] WP-API returned no posts for {section} (cat={cat_id})")
        return []

    articles: list[dict[str, Any]] = []
    for post in posts:
        if not isinstance(post, dict):
            continue
        row = _wp_post_to_article(post)
        if row:
            articles.append(row)
    if articles:
        print(f"[INFO] WP-API {section}: {len(articles)} article(s)")
    return articles


def fetch_main_wp_api(*, per_page: int = 20) -> list[dict[str, Any]]:
    """Site-wide latest posts via WP REST API."""
    posts = _fetch_wp_json(
        f"{BASE_URL}/wp-json/wp/v2/posts",
        params={
            "per_page": per_page,
            "_fields": _WP_POST_FIELDS,
            "orderby": "date",
            "order": "desc",
        },
    )
    if not isinstance(posts, list) or not posts:
        return []
    articles: list[dict[str, Any]] = []
    for post in posts:
        if not isinstance(post, dict):
            continue
        row = _wp_post_to_article(post)
        if row:
            articles.append(row)
    if articles:
        print(f"[INFO] WP-API main: {len(articles)} article(s)")
    return articles


def fetch_rss_bytes(url: str, *, timeout: int = 30) -> bytes | None:
    """Category/main RSS via plain Scrapling HTTP (no StealthyFetcher)."""
    return fetch_bytes(
        url,
        accept=_RSS_ACCEPT,
        timeout=timeout,
        expect_xml=True,
        extra_headers=_RSS_HEADERS,
    )


def fetch_category_html(url: str, *, timeout: int | None = None) -> str | None:
    if timeout is None:
        timeout = 45 if _is_ci() else 25
    page_url = url.rstrip("/") + "/"
    return fetch_text(
        page_url,
        timeout=timeout,
        extra_headers=_HEADERS,
        wait_selector=_LIST_WAIT,
        **_html_stealth_kw(),
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
    html = fetch_text(
        url,
        timeout=timeout,
        extra_headers=_HEADERS,
        wait_selector="article .entry-content, .entry-content, article",
        **_html_stealth_kw(),
    )
    if not html or is_cloudflare_block(html):
        return None
    return extract_article_content_from_html(html, url)


def parse_rss(xml_text: str) -> list[dict[str, Any]]:
    """Parse Thinakaran WordPress RSS (content:encoded + category feeds)."""
    ns = {
        "content": "http://purl.org/rss/1.0/modules/content/",
        "dc": "http://purl.org/dc/elements/1.1/",
        "media": "http://search.yahoo.com/mrss/",
    }
    articles: list[dict[str, Any]] = []
    root = ET.fromstring(xml_text)
    items = root.findall("./channel/item") or root.findall(".//item")
    for item in items:
        link = _normalize_rss_link(item.findtext("link") or "")
        if not is_article_url(link):
            continue

        title = html_module.unescape((item.findtext("title") or "").strip())
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
        body_text = _rss_html_to_plain(content_html) or _rss_html_to_plain(desc_html)

        image_url = ""
        thumb = item.find("media:thumbnail", ns)
        if thumb is not None and thumb.get("url"):
            image_url = thumb.get("url", "").strip()
        enclosure = item.find("enclosure")
        if (
            not image_url
            and enclosure is not None
            and (enclosure.get("type") or "").startswith("image")
        ):
            image_url = (enclosure.get("url") or "").strip()
        if not image_url:
            image_url = _first_article_image(content_html) or _first_article_image(
                desc_html
            )

        author_el = item.find("dc:creator", ns)
        author = html_module.unescape(
            (author_el.text or "").strip() if author_el is not None else ""
        )
        categories = [
            html_module.unescape((c.text or "").strip())
            for c in item.findall("category")
            if (c.text or "").strip()
        ]
        guid = (item.findtext("guid") or "").strip()

        row: dict[str, Any] = {
            "title": title,
            "link": link,
            "summary": body_text,
            "description": body_text,
            "date": date_str,
            "image_url": image_url,
            "date_source": f"RSS: {date_str}",
        }
        if author:
            row["author"] = author
        if categories:
            row["rss_categories"] = categories
        if guid:
            row["guid"] = guid
        articles.append(row)
    return articles


def _parse_rss_bytes(raw: bytes, *, label: str) -> list[dict[str, Any]]:
    xml_text = raw.decode("utf-8", errors="replace")
    if is_cloudflare_block(xml_text):
        print(f"[WARN] Cloudflare interstitial on {label}")
        return []
    try:
        items = parse_rss(xml_text)
        if items:
            print(f"[INFO] RSS {label}: {len(items)} article(s)")
        return items
    except Exception as e:
        print(f"[WARN] RSS parse error ({label}): {e}")
        return []


def _fetch_section_rss(section: str) -> list[dict[str, Any]]:
    timeout = 45 if _is_ci() else 30
    for feed_url in (
        f"{BASE_URL}/category/{section}/feed/",
        f"https://thinakaran.lk/category/{section}/feed/",
    ):
        raw = fetch_rss_bytes(feed_url, timeout=timeout)
        if not raw:
            print(f"[WARN] RSS fetch failed: {feed_url}")
            continue
        print(f"[INFO] RSS fetched {feed_url} ({len(raw)} bytes)")
        items = _parse_rss_bytes(raw, label=section)
        if items:
            return items
    return []


def fetch_section_feed(section: str) -> list[dict[str, Any]]:
    """Category feed: WP REST API on CI (RSS blocked); RSS then WP-API locally."""
    if _is_ci():
        items = fetch_section_wp_api(section)
        if items:
            return items
        return _fetch_section_rss(section)

    items = _fetch_section_rss(section)
    if items:
        return items
    print(f"[INFO] RSS empty for {section} — trying WP-API fallback")
    return fetch_section_wp_api(section)


def fetch_main_feed() -> list[dict[str, Any]]:
    """Site-wide feed: WP-API on CI, RSS fallback locally."""
    if _is_ci():
        items = fetch_main_wp_api()
        if items:
            return items
    timeout = 45 if _is_ci() else 30
    for feed_url in (f"{BASE_URL}/feed/", "https://thinakaran.lk/feed/"):
        raw = fetch_rss_bytes(feed_url, timeout=timeout)
        if not raw:
            continue
        print(f"[INFO] Main RSS fetched {feed_url} ({len(raw)} bytes)")
        items = _parse_rss_bytes(raw, label="main")
        if items:
            return items
    return fetch_main_wp_api()


# Back-compat alias for incremental_outlets
fetch_thinakaran_section_feed = fetch_section_feed
