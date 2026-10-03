"""Scrapling wrappers, plus small parsing helpers the scrapers share.

- get()/get_json(): Scrapling's Fetcher — fast HTTP that impersonates a real
  Chrome TLS fingerprint and headers. Used for YouTube, Instagram and X's
  embed endpoints.
- browser(): a Scrapling StealthySession — one stealth headless Chromium kept
  open across several pages (Facebook, X). It can also capture the JSON the
  page's own scripts fetch in the background (capture_xhr), which is sturdier
  than reading the rendered HTML.
- select(): CSS selection with Scrapling's adaptive mode. Elements found today
  are fingerprinted into a small SQLite file; if a site renames its markup
  later, Scrapling relocates them by similarity instead of finding nothing.
"""

from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterator

BROWSER_TIMEOUT_MS = 60_000
ADAPTIVE_DB = os.environ.get("SCRAPLING_ADAPTIVE_DB", ".scrapling/adaptive.db")


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


class Browser:
    """One open stealth browser. fetch() pages through it; each page keeps its captured XHR."""

    def __init__(self, session):
        self.session = session

    def fetch(self, url: str, page_action: Callable | None = None, wait_selector: str | None = None):
        Path(ADAPTIVE_DB).parent.mkdir(parents=True, exist_ok=True)
        page = self.session.fetch(
            url, page_action=page_action, wait_selector=wait_selector,
            selector_config={"adaptive": True, "storage_args": {"storage_file": ADAPTIVE_DB, "url": url}},
        )
        if page.status >= 400:
            raise ScrapeError(f"HTTP {page.status} from {url}")
        return page


@contextmanager
def browser(cookies: list[dict] | None = None, capture_xhr: str | None = None) -> Iterator[Browser]:
    from scrapling.fetchers import StealthySession

    extra = {"executable_path": os.environ["CHROMIUM_PATH"]} if os.environ.get("CHROMIUM_PATH") else {}
    try:
        with StealthySession(
            headless=True, network_idle=True, cookies=cookies, capture_xhr=capture_xhr,
            timeout=BROWSER_TIMEOUT_MS, block_webrtc=True, block_ads=True, **extra,
        ) as session:
            yield Browser(session)
    except ScrapeError:
        raise
    except Exception as e:  # browser failed to start or crashed
        raise ScrapeError(f"browser error: {type(e).__name__}: {e}") from e


def xhr_json(page) -> list:
    """Parse every captured background response; Facebook sends several JSON documents per response."""
    docs = []
    for resp in getattr(page, "captured_xhr", None) or []:
        try:
            body = resp.body if isinstance(resp.body, (bytes, str)) else resp.body()
        except Exception:  # noqa: BLE001 - a response we can't read is just skipped
            continue
        text = body.decode("utf-8", "replace") if isinstance(body, bytes) else body
        docs += json_documents(text.removeprefix("for (;;);"))
    return docs


def json_documents(text: str) -> list:
    """Decode one JSON document, or several separated by newlines."""
    try:
        return [json.loads(text)]
    except ValueError:
        pass
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(("{", "[")):
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def embedded_json(page) -> list:
    """JSON blobs inside <script type="application/json"> tags (how Facebook ships its first posts)."""
    docs = []
    for script in page.css('script[type="application/json"]'):
        docs += json_documents(str(script.text or ""))
    return docs


def select(node, selector: str, key: str):
    """node.css(selector), but adaptive when the page supports it (see module docstring)."""
    if not getattr(node, "_Selector__adaptive_enabled", False):
        return node.css(selector)
    found = node.css(selector, identifier=key, auto_save=True)
    if not found:
        found = node.css(selector, identifier=key, adaptive=True)
    return found


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


def iter_dicts(obj):
    """Every dict nested anywhere inside obj."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from iter_dicts(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from iter_dicts(v)


def first(values, default=None):
    return next(iter(values), default)
