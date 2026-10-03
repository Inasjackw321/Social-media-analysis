"""Instagram, scraped with Scrapling.

- Accounts in config.json (no login): the JSON Instagram's own web app loads
  for a profile page, which includes the 12 latest posts.
- Search terms as hashtags: Instagram only shows hashtag pages when logged in,
  so this needs the INSTAGRAM_SESSIONID secret (the `sessionid` cookie).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

from .. import scrape
from ..config import ScanConfig
from ..models import Post
from .base import NotConfigured, finish, is_recent, iso, matches_query

PROFILE = "https://www.instagram.com/api/v1/users/web_profile_info/"
TAG = "https://www.instagram.com/api/v1/tags/web_info/"
# The public app id Instagram's website sends with every request.
HEADERS = {"x-ig-app-id": "936619743392459", "x-requested-with": "XMLHttpRequest", "referer": "https://www.instagram.com/"}


def collect(cfg: ScanConfig) -> list[Post]:
    accounts = cfg.instagram.get("accounts", [])
    session = cfg.secret("INSTAGRAM_SESSIONID")
    if not accounts and not (session and cfg.query):
        raise NotConfigured("List accounts under instagram.accounts in config.json, or set INSTAGRAM_SESSIONID to search hashtags.")

    posts: list[Post] = []
    errors: list[str] = []
    if session:
        for tag in sorted({to_hashtag(t) for t in cfg.query} - {""}):
            try:
                posts += parse_tag(scrape.get_json(TAG, {"tag_name": tag}, HEADERS, {"sessionid": session}))
            except scrape.ScrapeError as e:
                errors.append(f"#{tag}: {e}")
    cookies = {"sessionid": session} if session else None
    for account in accounts:
        try:
            found = parse_profile(scrape.get_json(PROFILE, {"username": account}, HEADERS, cookies))
            posts += [p for p in found if matches_query(p.text, cfg.query)]
        except scrape.ScrapeError as e:
            errors.append(f"@{account}: {e}")

    return finish([p for p in posts if is_recent(p.created_at, cfg)], errors)


def to_hashtag(term: str) -> str:
    return re.sub(r"[^\w]", "", term.lower())


def _ts(seconds) -> str:
    return iso(datetime.fromtimestamp(int(seconds), timezone.utc)) if seconds else ""


def parse_profile(data: dict) -> list[Post]:
    user = (data.get("data") or {}).get("user") or {}
    if not user:
        raise scrape.ScrapeError("profile not found or login required")
    username = user.get("username", "")
    posts = []
    for edge in user.get("edge_owner_to_timeline_media", {}).get("edges", []):
        n = edge.get("node", {})
        captions = n.get("edge_media_to_caption", {}).get("edges", [])
        posts.append(Post(
            platform="instagram",
            id=n.get("id", n.get("shortcode", "")),
            url=f"https://www.instagram.com/p/{n.get('shortcode', '')}/",
            text=captions[0]["node"].get("text", "") if captions else "",
            author=f"@{username}",
            created_at=_ts(n.get("taken_at_timestamp")),
            likes=(n.get("edge_liked_by") or n.get("edge_media_preview_like") or {}).get("count", 0),
            comments=n.get("edge_media_to_comment", {}).get("count", 0),
            views=n.get("video_view_count", 0) or 0,
        ))
    return posts


def parse_tag(data: dict) -> list[Post]:
    posts = []
    for m in scrape.walk(data, "media"):
        if not isinstance(m, dict) or "code" not in m:
            continue
        user = (m.get("user") or {}).get("username", "")
        posts.append(Post(
            platform="instagram",
            id=str(m.get("pk") or m["code"]),
            url=f"https://www.instagram.com/p/{m['code']}/",
            text=(m.get("caption") or {}).get("text", ""),
            author=f"@{user}" if user else "",
            created_at=_ts(m.get("taken_at")),
            likes=m.get("like_count", 0) or 0,
            comments=m.get("comment_count", 0) or 0,
            views=m.get("play_count", 0) or m.get("view_count", 0) or 0,
        ))
    return posts
