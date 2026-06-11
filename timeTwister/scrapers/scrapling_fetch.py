"""
Shared Scrapling HTTP fetch (https://github.com/D4Vinci/Scrapling) with curl_cffi GHA fallback.
StealthyFetcher (solve_cloudflare) used when plain HTTP is blocked on CI / datacenter IPs.
"""
from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlencode

try:
    from scrapling.fetchers import Fetcher

    HAS_SCRAPLING = True
except ImportError:
    HAS_SCRAPLING = False
    Fetcher = None  # type: ignore[misc, assignment]

try:
    from scrapling.fetchers import StealthyFetcher

    HAS_STEALTHY = True
except ImportError:
    HAS_STEALTHY = False
    StealthyFetcher = None  # type: ignore[misc, assignment]

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


def _is_ci() -> bool:
    return os.getenv("CI", "").lower() in ("1", "true", "yes")


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


def _valid_response_body(
    body: bytes,
    *,
    expect_xml: bool = False,
    expect_json: bool = False,
) -> bool:
    if not body:
        return False
    min_len = 2 if expect_json else 500
    if len(body) < min_len:
        return False
    sample = body[:8000].decode("utf-8", errors="ignore")
    if expect_xml:
        if is_cloudflare_block(sample):
            return False
        return "<?xml" in sample or "<rss" in sample
    if expect_json:
        stripped = sample.lstrip()
        if not stripped.startswith(("[", "{")):
            return False
        # WP category lookups etc. are tiny JSON — not CF interstitials
        if len(body) >= 500 and is_cloudflare_block(sample):
            return False
        return True
    if is_cloudflare_block(sample):
        return False
    return True


def _fetch_bytes_http_fallback(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    accept: str | None = None,
    timeout: int = 20,
    expect_xml: bool = False,
    expect_json: bool = False,
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
                body, expect_xml=expect_xml, expect_json=expect_json
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
        if resp.status_code == 200 and _valid_response_body(
            body, expect_xml=expect_xml, expect_json=expect_json
        ):
            print(f"[INFO] requests OK: {url[:80]}")
            return body
        print(
            f"[WARN] requests bad response for {url[:70]} "
            f"(status={resp.status_code}, len={len(body)})"
        )
    except Exception as e:
        print(f"[WARN] requests failed: {e}")
    return None


def _page_to_bytes(page: Any) -> bytes:
    body = page.body or b""
    if body:
        return body
    html = page.html_content or ""
    return html.encode("utf-8", errors="replace") if html else b""


def _page_to_text(page: Any) -> str:
    html = page.html_content or ""
    if html:
        return html
    body = page.body or b""
    return body.decode("utf-8", errors="replace") if body else ""


def fetch_stealth_page(
    url: str,
    *,
    timeout_ms: int = 60_000,
    wait_selector: str | None = None,
    extra_headers: dict[str, str] | None = None,
    expect_xml: bool = False,
) -> Any | None:
    """Playwright-based fetch with automatic Cloudflare challenge solving."""
    if not HAS_STEALTHY or StealthyFetcher is None:
        print("[WARN] StealthyFetcher unavailable (scrapling[fetchers] + playwright)")
        return None

    kwargs: dict[str, Any] = {
        "headless": True,
        "solve_cloudflare": True,
        "timeout": timeout_ms,
        "network_idle": True,
        "block_webrtc": True,
        "hide_canvas": True,
        "retries": 2 if _is_ci() else 3,
    }
    if _is_ci():
        kwargs["real_chrome"] = True
    if wait_selector:
        kwargs["wait_selector"] = wait_selector
        kwargs["wait_selector_state"] = "attached"
    if extra_headers:
        kwargs["extra_headers"] = extra_headers

    print(f"[INFO] StealthyFetcher (solve_cloudflare): {url[:80]}")
    try:
        page = StealthyFetcher.fetch(url, **kwargs)
    except Exception as e:
        print(f"[WARN] StealthyFetcher failed for {url[:70]}: {e}")
        return None

    body = _page_to_bytes(page)
    status = getattr(page, "status", 200)
    if status and status >= 400:
        print(
            f"[WARN] StealthyFetcher bad status for {url[:70]} "
            f"(status={status}, len={len(body)})"
        )
        return None
    if not _valid_response_body(body, expect_xml=expect_xml):
        sample = _page_to_text(page)[:200]
        print(
            f"[WARN] StealthyFetcher blocked/empty for {url[:70]} "
            f"(len={len(body)}, sample={sample!r})"
        )
        return None
    print(f"[INFO] StealthyFetcher OK: {url[:80]} ({len(body)} bytes)")
    return page


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


def _resolve_url(url: str, params: dict[str, Any] | None) -> str:
    if not params:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}{urlencode(params)}"


def fetch_bytes(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    accept: str | None = None,
    timeout: int = 20,
    expect_xml: bool = False,
    expect_json: bool = False,
    extra_headers: dict[str, str] | None = None,
    stealth_fallback: bool = False,
    stealth_first: bool = False,
    wait_selector: str | None = None,
) -> bytes | None:
    full_url = _resolve_url(url, params)
    stealth_timeout_ms = max(timeout * 1000, 60_000)

    if not stealth_first:
        page = fetch_page(
            url,
            params=params,
            accept=accept,
            timeout=timeout,
            extra_headers=extra_headers,
        )
        if page:
            body = page.body or b""
            if _valid_response_body(
                body, expect_xml=expect_xml, expect_json=expect_json
            ):
                return body

        print(f"[INFO] Falling back to curl_cffi for {full_url[:80]}")
        raw = _fetch_bytes_http_fallback(
            url,
            params=params,
            accept=accept,
            timeout=timeout,
            expect_xml=expect_xml,
            expect_json=expect_json,
            extra_headers=extra_headers,
        )
        if raw:
            return raw

    if stealth_fallback or stealth_first:
        page = fetch_stealth_page(
            full_url,
            timeout_ms=stealth_timeout_ms,
            wait_selector=wait_selector,
            extra_headers=extra_headers,
            expect_xml=expect_xml,
        )
        if page:
            return _page_to_bytes(page)
    return None


def fetch_text(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    accept: str | None = None,
    timeout: int = 20,
    extra_headers: dict[str, str] | None = None,
    stealth_fallback: bool = False,
    stealth_first: bool = False,
    wait_selector: str | None = None,
) -> str | None:
    full_url = _resolve_url(url, params)
    stealth_timeout_ms = max(timeout * 1000, 60_000)

    if not stealth_first:
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

    if stealth_fallback or stealth_first:
        page = fetch_stealth_page(
            full_url,
            timeout_ms=stealth_timeout_ms,
            wait_selector=wait_selector,
            extra_headers=extra_headers,
        )
        if page:
            text = _page_to_text(page)
            if text and not is_cloudflare_block(text):
                return text
    return None


class HttpResponse:
    """requests-like wrapper for legacy _http_get callers."""

    def __init__(self, body: bytes):
        self.content = body
        self.text = body.decode("utf-8", errors="replace")
        self.status_code = 200
