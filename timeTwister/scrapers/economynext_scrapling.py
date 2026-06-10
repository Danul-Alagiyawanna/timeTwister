"""
EconomyNext fetch + parse via Scrapling (https://github.com/D4Vinci/Scrapling).
HTTP Fetcher with browser TLS impersonation — no Selenium/Chrome required.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

try:
    from scrapling.fetchers import Fetcher
    from scrapling.parser import Selector

    HAS_SCRAPLING = True
except ImportError:
    HAS_SCRAPLING = False
    Fetcher = None  # type: ignore[misc, assignment]
    Selector = None  # type: ignore[misc, assignment]

BASE_URL = "https://economynext.com"
IMPERSONATE_PROFILES = ("chrome", "chrome124", "firefox133", "safari17_0")


def is_cloudflare_block(html: str) -> bool:
    if not html or len(html) < 200:
        return True
    markers = (
        "cf-browser-verification",
        "Just a moment",
        "Attention Required! | Cloudflare",
        "Enable JavaScript and cookies",
    )
    lower = html[:8000].lower()
    return any(m.lower() in lower for m in markers)


def fetch_page(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    accept: str | None = None,
    timeout: int = 20,
) -> Any | None:
    """Fetch URL with Scrapling; try several TLS impersonation profiles."""
    if not HAS_SCRAPLING:
        print("[ERROR] scrapling not installed — pip install 'scrapling[fetchers]'")
        return None

    if params:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}{urlencode(params)}"

    headers: dict[str, str] = {}
    if accept:
        headers["Accept"] = accept

    last_err: Exception | None = None
    for profile in IMPERSONATE_PROFILES:
        try:
            page = Fetcher.get(
                url,
                impersonate=profile,
                stealthy_headers=True,
                timeout=timeout,
                headers=headers or None,
            )
            status = getattr(page, "status", 200)
            body = page.body or b""
            sample = (page.html_content or "") or body[:8000].decode(
                "utf-8", errors="ignore"
            )
            if status == 200 and body and not is_cloudflare_block(sample):
                print(f"[INFO] Scrapling ({profile}) OK: {url[:80]}")
                return page
            print(
                f"[WARN] Scrapling ({profile}) blocked/empty for {url[:70]} "
                f"(status={status}, len={len(body)})"
            )
        except Exception as e:
            last_err = e
            print(f"[WARN] Scrapling ({profile}) failed: {e}")
    if last_err:
        print(f"[WARN] All Scrapling profiles failed for {url[:70]}: {last_err}")
    return None


def fetch_bytes(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    accept: str | None = None,
    timeout: int = 20,
) -> bytes | None:
    """Raw response body (use for RSS/XML and JSON API endpoints)."""
    page = fetch_page(url, params=params, accept=accept, timeout=timeout)
    if not page:
        return None
    body = page.body or b""
    if not body:
        return None
    if is_cloudflare_block(body[:8000].decode("utf-8", errors="ignore")):
        return None
    return body


def fetch_text(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    accept: str | None = None,
    timeout: int = 20,
) -> str | None:
    """HTML text for article/list pages (uses Scrapling DOM html_content)."""
    page = fetch_page(url, params=params, accept=accept, timeout=timeout)
    if not page:
        return None
    html = page.html_content or ""
    if html and not is_cloudflare_block(html):
        return html
    body = page.body or b""
    if body and not is_cloudflare_block(body[:8000].decode("utf-8", errors="ignore")):
        return body.decode("utf-8", errors="replace")
    return None


def _selector(html: str) -> Any:
    return Selector(html)


def extract_article_metadata_from_html(html: str, url: str) -> dict[str, Any]:
    """Extract article metadata from EconomyNext article HTML."""
    try:
        page = _selector(html)
        date_published = None

        date_meta = page.css('meta[property="article:published_time"]::attr(content)').get()
        if date_meta:
            try:
                if "," in date_meta:
                    date_part = date_meta.split(",", 1)[1].strip()
                    for fmt in ("%A %B %d, %Y", "%A %b %d, %Y"):
                        try:
                            date_published = datetime.strptime(date_part, fmt)
                            break
                        except ValueError:
                            continue
            except Exception:
                pass

        if not date_published:
            for script_text in page.css('script[type="application/ld+json"]::text').getall():
                try:
                    data = json.loads(script_text)
                    if isinstance(data, dict) and data.get("datePublished"):
                        date_str = data["datePublished"]
                        date_published = datetime.fromisoformat(
                            date_str.replace("Z", "+00:00").replace("+00:00", "")
                        )
                        break
                except (json.JSONDecodeError, ValueError, TypeError):
                    continue

        image_url = page.css('meta[property="og:image"]::attr(content)').get() or ""
        title = page.css('meta[property="og:title"]::attr(content)').get() or ""

        description = ""
        for block in page.css(".story-page-text-content"):
            classes = block.attrib.get("class", "")
            if "most-recent-article-text" in classes:
                continue
            paras = [
                t.strip()
                for t in block.css("p::text").getall()
                if t and t.strip()
            ]
            if paras:
                description = "\n\n".join(paras)
                break
            block_text = block.get_all_text(strip=True)
            if block_text:
                description = block_text
                break

        if not description or len(description) < 100:
            for xpath in (
                "/html/body/div[3]/div[2]/div/div/div[5]/div",
                "/html/body/div[3]/div[2]/div/div/div[6]/div",
            ):
                xpath_desc = " ".join(page.xpath(f"{xpath}//text()").getall()).strip()
                if xpath_desc and len(xpath_desc) > len(description):
                    description = xpath_desc
                    break

        if not description:
            description = (
                page.css('meta[property="og:description"]::attr(content)').get()
                or page.css('meta[name="twitter:description"]::attr(content)').get()
                or ""
            )

        return {
            "date_published": date_published,
            "image_url": image_url,
            "description": description,
            "title": title,
            "link": url,
        }
    except Exception as e:
        print(f"     Error extracting metadata: {e}")
        return {
            "date_published": None,
            "image_url": "",
            "description": "",
            "title": "",
            "link": url,
        }


def fetch_article_metadata(url: str) -> dict[str, Any]:
    html = fetch_text(url, timeout=25)
    if not html:
        return extract_article_metadata_from_html("", url)
    meta = extract_article_metadata_from_html(html, url)
    if not meta.get("description") or len(meta.get("description", "")) < 100:
        time.sleep(2)
        html2 = fetch_text(url, timeout=25)
        if html2:
            meta = extract_article_metadata_from_html(html2, url)
    return meta


def parse_homepage_article_links(html: str) -> list[str]:
    links_with_ids: list[tuple[str, int]] = []
    page = _selector(html)
    for href in page.css("a::attr(href)").getall():
        href = (href or "").strip()
        if not href:
            continue
        if not href.startswith("http"):
            if href.startswith("/"):
                href = BASE_URL + href
            else:
                continue
        match = re.search(r"-(\d+)/?$", href)
        if match:
            post_id = int(match.group(1))
            if (href, post_id) not in links_with_ids:
                links_with_ids.append((href, post_id))
    if not links_with_ids:
        return []
    max_id = max(post_id for _, post_id in links_with_ids)
    threshold = max_id - 800
    return [url for url, pid in links_with_ids if pid >= threshold]


def parse_list_page_cards(html: str) -> list[dict[str, str]]:
    page = _selector(html)
    cards: list[dict[str, str]] = []
    for card in page.css(".story-grid-single-story"):
        title = ""
        link = ""
        for sel in ("h3.recent-top-header a", "h3 a", "a"):
            title = card.css(f"{sel}::text").get() or ""
            link = card.css(f"{sel}::attr(href)").get() or ""
            if link:
                break
        date_text = card.css(".article-publish-date::text").get() or ""
        image_url = (
            card.css("img::attr(src)").get()
            or card.css("amp-img::attr(src)").get()
            or ""
        )
        if link:
            cards.append(
                {
                    "title": title.strip(),
                    "link": link.strip(),
                    "date_text": date_text.strip(),
                    "image_url": image_url.strip(),
                }
            )
    return cards
