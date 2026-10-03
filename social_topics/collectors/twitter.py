"""X / Twitter, scraped with Scrapling.

- Accounts in config.json: first X's public embed timeline
  (syndication.twitter.com, fast HTTP). If that's empty or blocked, a stealth
  browser opens x.com/<account> and captures the timeline JSON the page loads
  in the background (Scrapling's capture_xhr).
- Search terms: X only allows search when logged in, so this needs the
  X_AUTH_TOKEN secret (the `auth_token` cookie from a logged-in browser).
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
GRAPHQL = r"/graphql/[^/]+/(UserTweets|UserMedia|SearchTimeline|TweetDetail)"
TWEET = 'article[data-testid="tweet"]'


def collect(cfg: ScanConfig) -> list[Post]:
    accounts = cfg.twitter.get("accounts", [])
    auth = cfg.secret("X_AUTH_TOKEN")
    if not accounts and not (auth and cfg.query):
        raise NotConfigured("List accounts under twitter.accounts in config.json, or set X_AUTH_TOKEN to search by keyword.")

    posts: list[Post] = []
    errors: list[str] = []
    needs_browser: list[str] = []
    read = 0
    for user in accounts:
        try:
            page = scrape.get(SYNDICATION.format(user=urllib.parse.quote(user)))
            found = parse_syndication(page.body.decode("utf-8", "replace"))
        except scrape.ScrapeError:
            found = []
        read += len(found)
        if found:
            posts += [p for p in found if matches_query(p.text, cfg.query)]
        else:
            needs_browser.append(user)

    scrolls = int(cfg.twitter.get("scrolls", 5))
    targets = [(f"https://x.com/{urllib.parse.quote(u)}", f"@{u}", True) for u in needs_browser]
    if cfg.query and auth:
        q = urllib.parse.urlencode({"q": build_query(cfg.query, cfg.lookback_hours), "f": "live"})
        targets.append((f"https://x.com/search?{q}", "search", False))
    if targets:
        cookies = {"auth_token": auth, **({"ct0": cfg.secret("X_CT0")} if cfg.secret("X_CT0") else {})} if auth else None
        try:
            with scrape.browser(scrape.browser_cookies(cookies, ".x.com") if cookies else None, capture_xhr=GRAPHQL) as b:
                for url, label, filter_by_query in targets:
                    try:
                        page = b.fetch(url, page_action=scrape.scroll(scrolls))
                    except scrape.ScrapeError as e:
                        errors.append(f"{label}: {e}")
                        continue
                    found = parse_graphql(scrape.xhr_json(page)) or parse_search_page(page)
                    if not found:
                        errors.append(f"{label}: no tweets visible (X may require login)")
                    read += len(found)
                    posts += [p for p in found if not filter_by_query or matches_query(p.text, cfg.query)]
        except scrape.ScrapeError as e:
            errors.append(str(e))

    return finish([p for p in posts if is_recent(p.created_at, cfg)], errors, read, cfg)


def build_query(terms: list[str], lookback_hours: int) -> str:
    parts = [f'"{t}"' if " " in t else t for t in terms]
    since = datetime.now(timezone.utc).timestamp() - lookback_hours * 3600
    return f"({' OR '.join(parts)}) -filter:retweets since_time:{int(since)}"


def parse_search_page(page) -> list[Post]:
    posts = []
    for art in scrape.select(page, TWEET, "x-tweet"):
        time_el = art.css("time").first
        link = time_el.parent.attrib.get("href", "") if time_el is not None else ""
        m = re.match(r"/([^/]+)/status/(\d+)", link)
        if not m:
            continue
        text_el = scrape.select(art, 'div[data-testid="tweetText"]', "x-tweet-text").first
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
            images=[i.attrib["src"] for i in art.css('div[data-testid="tweetPhoto"] img') if i.attrib.get("src")],
        ))
    return posts


def parse_graphql(docs: list) -> list[Post]:
    """Tweets from X's own GraphQL responses (timeline, search, tweet detail)."""
    posts = {}
    for doc in docs:
        for res in scrape.walk(doc, "tweet_results"):
            r = res.get("result") if isinstance(res, dict) else None
            if r and r.get("__typename") == "TweetWithVisibilityResults":
                r = r.get("tweet")
            legacy = (r or {}).get("legacy") or {}
            if "id_str" not in legacy:
                continue
            user = scrape.first(scrape.walk(r.get("core", {}), "screen_name"), "")
            note = scrape.first(scrape.walk(r.get("note_tweet", {}), "text"), "")
            media = legacy.get("extended_entities", {}).get("media") or legacy.get("entities", {}).get("media", [])
            posts[legacy["id_str"]] = Post(
                platform="twitter",
                id=legacy["id_str"],
                url=f"https://x.com/{user or 'i'}/status/{legacy['id_str']}",
                text=note or legacy.get("full_text", ""),
                author=f"@{user}" if user else "",
                created_at=_twitter_time(legacy.get("created_at", "")),
                likes=legacy.get("favorite_count", 0) or 0,
                comments=legacy.get("reply_count", 0) or 0,
                shares=(legacy.get("retweet_count", 0) or 0) + (legacy.get("quote_count", 0) or 0),
                views=int((r.get("views") or {}).get("count", 0) or 0),
                images=[m["media_url_https"] for m in media if m.get("media_url_https")],
            )
    return list(posts.values())


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
            images=list(dict.fromkeys(scrape.walk(t, "media_url_https"))),
        ))
    return posts


def _twitter_time(value: str) -> str:
    try:
        return iso(datetime.strptime(value, "%a %b %d %H:%M:%S %z %Y"))
    except ValueError:
        return ""
