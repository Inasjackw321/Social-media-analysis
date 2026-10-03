"""Scrapling wrappers, plus small parsing helpers the scrapers share.

- get()/get_json(): Scrapling's Fetcher — fast HTTP that impersonates a real
  Chrome TLS fingerprint and headers. Used for YouTube, Instagram and X's
  embed endpoints.
- browse(): Scrapling's StealthyFetcher — a stealth headless Chromium for pages
  that only render with JavaScript (Facebook, logged-in X search).
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Callable

BROWSER_TIMEOUT_MS = 60_000


class ScrapeError(RuntimeError):
    pass


def get(url: str, params: dict | None = None, headers: dict | None = None, cookies: dict | None = None):
    from scrapling.fetchers import Fetcher

    page = Fetcher.get(
        url, params=params, headers=headers, cookies=cookies,
        impersonate="chrome", stealthy_headers=True, timeout=30, retries=2,
    )
    if page.status >= 400:
        raise ScrapeError(f"HTTP {page.status} from {url}")
    return page


def get_json(url: str, params: dict | None = None, headers: dict | None = None, cookies: dict | None = None) -> dict:
    page = get(url, params, headers, cookies)
    try:
        return json.loads(page.body)
    except ValueError:
        raise ScrapeError(f"{url} did not return JSON (probably a login wall or rate limit)") from None


def browse(
    url: str,
    cookies: list[dict] | None = None,
    page_action: Callable | None = None,
    wait_selector: str | None = None,
):
    from scrapling.fetchers import StealthyFetcher

    extra = {"executable_path": os.environ["CHROMIUM_PATH"]} if os.environ.get("CHROMIUM_PATH") else {}
    page = StealthyFetcher.fetch(
        url, headless=True, network_idle=True, cookies=cookies, page_action=page_action,
        wait_selector=wait_selector, timeout=BROWSER_TIMEOUT_MS, block_webrtc=True, **extra,
    )
    if page.status >= 400:
        raise ScrapeError(f"HTTP {page.status} from {url}")
    return page


def scroll(times: int = 4, pause_ms: int = 1500) -> Callable:
    """A page_action that scrolls down to load more posts."""

    def action(page):
        page.keyboard.press("Escape")  # dismiss login/cookie pop-ups if any
        for _ in range(times):
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(pause_ms)
        return page

    return action


def cookie_header_to_dict(header: str) -> dict[str, str]:
    """'a=1; b=2' -> {'a': '1', 'b': '2'}"""
    out = {}
    for part in (header or "").split(";"):
        name, sep, value = part.strip().partition("=")
        if sep and name:
            out[name.strip()] = value.strip()
    return out


def browser_cookies(cookies: dict[str, str], domain: str) -> list[dict]:
    return [{"name": k, "value": v, "domain": domain, "path": "/", "secure": True} for k, v in cookies.items()]


_COUNT_RE = re.compile(r"([\d][\d,.]*)\s*([KMB])?", re.I)
_MULT = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}


def parse_count(text: str | None) -> int:
    """'1.2K' -> 1200, '3,456 views' -> 3456, '' -> 0"""
    m = _COUNT_RE.search(text or "")
    if not m:
        return 0
    num, suffix = m.group(1), (m.group(2) or "").upper()
    if suffix:
        return int(float(num.replace(",", "")) * _MULT[suffix])
    return int(num.replace(",", "").replace(".", ""))


_AGO_RE = re.compile(r"(\d+)\s*(second|minute|hour|day|week|month|year)s?\s+ago", re.I)
_UNIT_HOURS = {"second": 1 / 3600, "minute": 1 / 60, "hour": 1, "day": 24, "week": 168, "month": 720, "year": 8760}


def relative_time(text: str | None, now: datetime | None = None) -> str:
    """'3 hours ago' -> ISO timestamp (approximate). Unknown -> ''."""
    m = _AGO_RE.search(text or "")
    if not m:
        return ""
    now = now or datetime.now(timezone.utc)
    dt = now - timedelta(hours=int(m.group(1)) * _UNIT_HOURS[m.group(2).lower()])
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def extract_json_after(html: str, marker: str) -> dict | None:
    """Pull the JSON object that follows e.g. 'var ytInitialData = ' out of a page."""
    start = html.find(marker)
    if start < 0:
        return None
    start = html.find("{", start + len(marker))
    try:
        obj, _ = json.JSONDecoder().raw_decode(html[start:])
    except ValueError:
        return None
    return obj


def walk(obj, key: str):
    """Yield every value stored under `key`, anywhere in nested dicts/lists."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                yield v
            yield from walk(v, key)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk(v, key)
