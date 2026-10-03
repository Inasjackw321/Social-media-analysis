"""YouTube, scraped with Scrapling — no API key or login needed.

- Search terms: scrapes youtube.com/results (filtered by upload date) and reads
  the video list out of the page's embedded `ytInitialData` JSON.
- Channels in config.json: reads each channel's public RSS feed.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from .. import scrape
from ..config import ScanConfig
from ..models import Post
from .base import NotConfigured, finish, is_recent, matches_query, normalize_time

SEARCH = "https://www.youtube.com/results"
RSS = "https://www.youtube.com/feeds/videos.xml"
CONSENT = {"SOCS": "CAI", "CONSENT": "YES+"}  # skip the EU cookie-consent interstitial
NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}


def upload_filter(lookback_hours: int) -> str:
    """YouTube's 'sp' search parameter for: last hour / today / this week / this month."""
    if lookback_hours <= 1:
        return "EgIIAQ=="
    if lookback_hours <= 24:
        return "EgIIAg=="
    if lookback_hours <= 24 * 7:
        return "EgIIAw=="
    return "EgIIBA=="


def collect(cfg: ScanConfig) -> list[Post]:
    channels = cfg.youtube.get("channels", [])
    if not cfg.query and not channels:
        raise NotConfigured("Give search terms, or list channel IDs under youtube.channels in config.json.")

    posts: list[Post] = []
    errors: list[str] = []
    for term in cfg.query:
        try:
            page = scrape.get(SEARCH, {"search_query": term, "sp": upload_filter(cfg.lookback_hours)}, cookies=CONSENT)
            posts += parse_search(page.body.decode("utf-8", "replace"))[: int(cfg.youtube.get("max_results_per_term", 30))]
        except scrape.ScrapeError as e:
            errors.append(f"search '{term}': {e}")
    for channel in channels:
        try:
            feed = parse_rss(scrape.get(RSS, {"channel_id": channel}).body)
            posts += [p for p in feed if matches_query(p.text, cfg.query)]
        except (scrape.ScrapeError, ET.ParseError) as e:
            errors.append(f"channel {channel}: {e}")

    return finish([p for p in posts if is_recent(p.created_at, cfg)], errors)


def _text(node: dict | None) -> str:
    if not node:
        return ""
    if "simpleText" in node:
        return node["simpleText"]
    return "".join(r.get("text", "") for r in node.get("runs", []))


def parse_search(html: str) -> list[Post]:
    data = scrape.extract_json_after(html, "var ytInitialData = ") or scrape.extract_json_after(html, 'ytInitialData"] = ')
    if not data:
        raise scrape.ScrapeError("YouTube search page had no ytInitialData (layout change or bot check)")
    posts = []
    for v in scrape.walk(data, "videoRenderer"):
        vid = v.get("videoId")
        if not vid:
            continue
        snippet = " ".join(_text(s.get("snippetText")) for s in v.get("detailedMetadataSnippets", []))
        posts.append(Post(
            platform="youtube",
            id=vid,
            url=f"https://www.youtube.com/watch?v={vid}",
            text=f"{_text(v.get('title'))}\n{snippet or _text(v.get('descriptionSnippet'))}".strip(),
            author=_text(v.get("ownerText")) or _text(v.get("longBylineText")),
            created_at=scrape.relative_time(_text(v.get("publishedTimeText"))),
            views=scrape.parse_count(_text(v.get("viewCountText"))),
        ))
    return posts


def parse_rss(xml_bytes: bytes) -> list[Post]:
    root = ET.fromstring(xml_bytes)
    posts = []
    for entry in root.findall("atom:entry", NS):
        vid = entry.findtext("yt:videoId", "", NS)
        title = entry.findtext("atom:title", "", NS)
        desc = entry.findtext("media:group/media:description", "", NS)
        stats = entry.find("media:group/media:community/media:statistics", NS)
        rating = entry.find("media:group/media:community/media:starRating", NS)
        posts.append(Post(
            platform="youtube",
            id=vid,
            url=f"https://www.youtube.com/watch?v={vid}",
            text=f"{title}\n{(desc or '')[:500]}".strip(),
            author=entry.findtext("atom:author/atom:name", "", NS),
            created_at=normalize_time(entry.findtext("atom:published", "", NS)),
            views=int(stats.get("views", 0)) if stats is not None else 0,
            likes=int(rating.get("count", 0)) if rating is not None else 0,
        ))
    return posts
