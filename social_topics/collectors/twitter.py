"""X / Twitter, scraped with Scrapling.

- Accounts in config.json (no login): X's public embed timeline
  (syndication.twitter.com), which ships the tweets as JSON inside the page.
- Search terms: X only allows search when logged in, so this needs the
  X_AUTH_TOKEN secret (the `auth_token` cookie from a logged-in browser).
  A stealth browser then loads x.com/search and reads the tweets off the page.
"""

from __future__ import annotations

import re
import urllib.parse
from datetime import datetime, timezone

from .. import scrape
from ..config import ScanConfig
from ..models import Post
from .base import NotConfigured, finish, is_recent, iso, matches_query

SYNDICATION = "https://syndication.twitter.com/srv/timeline-profile/screen-name/{user}"


def collect(cfg: ScanConfig) -> list[Post]:
    accounts = cfg.twitter.get("accounts", [])
    auth = cfg.secret("X_AUTH_TOKEN")
    if not accounts and not (auth and cfg.query):
        raise NotConfigured("List accounts under twitter.accounts in config.json, or set X_AUTH_TOKEN to search by keyword.")

    posts: list[Post] = []
    errors: list[str] = []
    if cfg.query and auth:
        try:
            posts += search(cfg.query, auth, cfg.secret("X_CT0"), cfg.lookback_hours, int(cfg.twitter.get("scrolls", 5)))
        except scrape.ScrapeError as e:
            errors.append(f"search: {e}")
    for user in accounts:
        try:
            page = scrape.get(SYNDICATION.format(user=urllib.parse.quote(user)))
            posts += [p for p in parse_syndication(page.body.decode("utf-8", "replace")) if matches_query(p.text, cfg.query)]
        except scrape.ScrapeError as e:
            errors.append(f"@{user}: {e}")

    return finish([p for p in posts if is_recent(p.created_at, cfg)], errors)


def build_query(terms: list[str], lookback_hours: int) -> str:
    parts = [f'"{t}"' if " " in t else t for t in terms]
    since = datetime.now(timezone.utc).timestamp() - lookback_hours * 3600
    return f"({' OR '.join(parts)}) -filter:retweets since_time:{int(since)}"


def search(terms: list[str], auth_token: str, ct0: str, lookback_hours: int, scrolls: int) -> list[Post]:
    cookies = {"auth_token": auth_token, **({"ct0": ct0} if ct0 else {})}
    url = "https://x.com/search?" + urllib.parse.urlencode({"q": build_query(terms, lookback_hours), "f": "top"})
    page = scrape.browse(
        url,
        cookies=scrape.browser_cookies(cookies, ".x.com"),
        page_action=scrape.scroll(scrolls),
        wait_selector='article[data-testid="tweet"]',
    )
    posts = parse_search_page(page)
    if not posts:
        raise scrape.ScrapeError("x.com search showed no tweets (is X_AUTH_TOKEN still valid?)")
    return posts


def parse_search_page(page) -> list[Post]:
    posts = []
    for art in page.css('article[data-testid="tweet"]'):
        time_el = art.css("time").first
        link = time_el.parent.attrib.get("href", "") if time_el is not None else ""
        m = re.match(r"/([^/]+)/status/(\d+)", link)
        if not m:
            continue
        text_el = art.css('div[data-testid="tweetText"]').first
        group = art.css('div[role="group"]').first
        stats = group.attrib.get("aria-label", "") if group is not None else ""
        posts.append(Post(
            platform="twitter",
            id=m.group(2),
            url=f"https://x.com{link}",
            text=text_el.get_all_text(separator=" ") if text_el is not None else "",
            author=f"@{m.group(1)}",
            created_at=time_el.attrib.get("datetime", "").replace(".000Z", "Z"),
            comments=_metric(stats, "repl"),
            shares=_metric(stats, "repost") + _metric(stats, "quote"),
            likes=_metric(stats, "like"),
            views=_metric(stats, "view"),
        ))
    return posts


def _metric(label: str, word: str) -> int:
    """'5 replies, 20 reposts, 1,204 likes, 3 bookmarks, 50K views' -> number before `word`."""
    m = re.search(r"([\d.,]+[KMB]?)\s+" + word, label, re.I)
    return scrape.parse_count(m.group(1)) if m else 0


def parse_syndication(html: str) -> list[Post]:
    data = scrape.extract_json_after(html, '<script id="__NEXT_DATA__" type="application/json">')
    if not data:
        raise scrape.ScrapeError("embed timeline had no tweet data (account private, suspended, or rate limited)")
    posts = []
    for t in scrape.walk(data, "tweet"):
        if not isinstance(t, dict) or "id_str" not in t:
            continue
        user = t.get("user", {}).get("screen_name", "")
        posts.append(Post(
            platform="twitter",
            id=t["id_str"],
            url=f"https://x.com/{user or 'i'}/status/{t['id_str']}",
            text=t.get("full_text") or t.get("text", ""),
            author=f"@{user}" if user else "",
            created_at=_twitter_time(t.get("created_at", "")),
            likes=t.get("favorite_count", 0) or 0,
            comments=t.get("reply_count", 0) or 0,
            shares=(t.get("retweet_count", 0) or 0) + (t.get("quote_count", 0) or 0),
        ))
    return posts


def _twitter_time(value: str) -> str:
    try:
        return iso(datetime.strptime(value, "%a %b %d %H:%M:%S %z %Y"))
    except ValueError:
        return ""
