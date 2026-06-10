"""
Shared Scrapling HTTP fetch (https://github.com/D4Vinci/Scrapling) with curl_cffi GHA fallback.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

try:
    from scrapling.fetchers import Fetcher

    HAS_SCRAPLING = True
except ImportError:
    HAS_SCRAPLING = False
    Fetcher = None  # type: ignore[misc, assignment]

IMPERSONATE_PROFILES = ("chrome", "chrome124", "firefox133", "safari17_0")
CURL_CFFI_PROFILES = ("chrome124", "safari17_0", "firefox133")
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def is_cloudflare_block(html: str) -> bool:
    if not html or len(html) < 500:
        return True
    markers = (
        "cf-browser-verification",
        "Just a moment",
        "Attention Required! | Cloudflare",
        "Enable JavaScript and cookies",
    )
    lower = html[:8000].lower()
    return any(m.lower() in lower for m in markers)


def _valid_response_body(body: bytes, *, expect_xml: bool = False) -> bool:
    if not body or len(body) < 500:
        return False
    sample = body[:8000].decode("utf-8", errors="ignore")
    if is_cloudflare_block(sample):
        return False
    if expect_xml:
        return "<?xml" in sample or "<rss" in sample
    return True


def _fetch_bytes_http_fallback(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    accept: str | None = None,
    timeout: int = 20,
    expect_xml: bool = False,
    extra_headers: dict[str, str] | None = None,
) -> bytes | None:
    headers = dict(_BROWSER_HEADERS)
    if extra_headers:
        headers.update(extra_headers)
    if accept:
        headers["Accept"] = accept

    for profile in CURL_CFFI_PROFILES:
        try:
            from curl_cffi import requests as cf_req  # type: ignore

            resp = cf_req.get(
                url,
                params=params,
                impersonate=profile,
                timeout=timeout,
                headers=headers,
            )
            body = resp.content or b""
            if resp.status_code == 200 and _valid_response_body(
                body, expect_xml=expect_xml
            ):
                print(f"[INFO] curl_cffi ({profile}) OK: {url[:80]}")
                return body
            print(
                f"[WARN] curl_cffi ({profile}) bad response for {url[:70]} "
                f"(status={resp.status_code}, len={len(body)})"
            )
        except ImportError:
            break
        except Exception as e:
            print(f"[WARN] curl_cffi ({profile}) failed: {e}")

    import requests as req

    try:
        resp = req.get(
            url,
            params=params,
            timeout=timeout,
            allow_redirects=True,
            headers=headers,
        )
        body = resp.content or b""
        if resp.status_code == 200 and _valid_response_body(body, expect_xml=expect_xml):
            print(f"[INFO] requests OK: {url[:80]}")
            return body
        print(
            f"[WARN] requests bad response for {url[:70]} "
            f"(status={resp.status_code}, len={len(body)})"
        )
    except Exception as e:
        print(f"[WARN] requests failed: {e}")
    return None


def fetch_page(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    accept: str | None = None,
    timeout: int = 20,
    extra_headers: dict[str, str] | None = None,
) -> Any | None:
    if not HAS_SCRAPLING:
        return None

    req_url = url
    if params:
        sep = "&" if "?" in url else "?"
        req_url = f"{url}{sep}{urlencode(params)}"

    headers: dict[str, str] = dict(extra_headers or {})
    if accept:
        headers["Accept"] = accept

    last_err: Exception | None = None
    for profile in IMPERSONATE_PROFILES:
        try:
            page = Fetcher.get(
                req_url,
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
                print(f"[INFO] Scrapling ({profile}) OK: {req_url[:80]}")
                return page
            print(
                f"[WARN] Scrapling ({profile}) blocked/empty for {req_url[:70]} "
                f"(status={status}, len={len(body)})"
            )
        except Exception as e:
            last_err = e
            print(f"[WARN] Scrapling ({profile}) failed: {e}")
    if last_err:
        print(f"[WARN] All Scrapling Fetcher profiles failed for {req_url[:70]}: {last_err}")
    return None


def fetch_bytes(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    accept: str | None = None,
    timeout: int = 20,
    expect_xml: bool = False,
    extra_headers: dict[str, str] | None = None,
) -> bytes | None:
    if params:
        sep = "&" if "?" in url else "?"
        full_url = f"{url}{sep}{urlencode(params)}"
    else:
        full_url = url

    page = fetch_page(
        url,
        params=params,
        accept=accept,
        timeout=timeout,
        extra_headers=extra_headers,
    )
    if page:
        body = page.body or b""
        if _valid_response_body(body, expect_xml=expect_xml):
            return body

    print(f"[INFO] Falling back to curl_cffi for {full_url[:80]}")
    return _fetch_bytes_http_fallback(
        url,
        params=params,
        accept=accept,
        timeout=timeout,
        expect_xml=expect_xml,
        extra_headers=extra_headers,
    )


def fetch_text(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    accept: str | None = None,
    timeout: int = 20,
    extra_headers: dict[str, str] | None = None,
) -> str | None:
    page = fetch_page(
        url,
        params=params,
        accept=accept,
        timeout=timeout,
        extra_headers=extra_headers,
    )
    if page:
        html = page.html_content or ""
        if html and not is_cloudflare_block(html):
            return html
        body = page.body or b""
        if body and _valid_response_body(body):
            return body.decode("utf-8", errors="replace")

    raw = _fetch_bytes_http_fallback(
        url,
        params=params,
        accept=accept or "text/html,application/xhtml+xml,*/*",
        timeout=timeout,
        extra_headers=extra_headers,
    )
    if raw:
        return raw.decode("utf-8", errors="replace")
    return None


class HttpResponse:
    """requests-like wrapper for legacy _http_get callers."""

    def __init__(self, body: bytes):
        self.content = body
        self.text = body.decode("utf-8", errors="replace")
        self.status_code = 200
