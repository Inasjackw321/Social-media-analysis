"""Facebook, scraped with Scrapling's stealth browser.

All Pages and searches share one StealthySession (one browser). For each page
it reads posts from:
1. the JSON Facebook embeds in the page and fetches while scrolling (captured
   with Scrapling's capture_xhr). This includes post dates and photos.
2. the rendered HTML, using adaptive selectors, if no JSON posts were found.

- Without login, Facebook shows a limited number of posts from public Pages
  (and sometimes a login wall instead).
- With the FACEBOOK_COOKIES secret ("c_user=...; xs=..." from a logged-in
  browser), it sees full Page feeds and can also search posts by keyword.
"""

from __future__ import annotations

import hashlib
import re
import urllib.parse
from datetime import datetime, timezone

from .. import scrape
from ..config import ScanConfig
from ..models import Post
from .base import NotConfigured, finish, is_recent, iso, matches_query

ARTICLE = 'div[role="article"]'
MESSAGE = 'div[data-ad-preview="message"], div[data-ad-comet-preview="message"]'
POST_LINK = re.compile(r"/(posts|videos|reel|photos|permalink\.php|story\.php)[/?]|story_fbid=|/p/")
GRAPHQL = r"/api/graphql/"


def collect(cfg: ScanConfig) -> list[Post]:
    pages = cfg.facebook.get("pages", [])
    cookies = scrape.cookie_header_to_dict(cfg.secret("FACEBOOK_COOKIES"))
    if not pages and not (cookies and cfg.query):
        raise NotConfigured("List Pages under facebook.pages in config.json, or set FACEBOOK_COOKIES to search by keyword.")

    scrolls = int(cfg.facebook.get("scrolls", 5))
    posts: list[Post] = []
    errors: list[str] = []
    read = 0
    targets = [(f"https://www.facebook.com/{urllib.parse.quote(p)}", p, True) for p in pages]
    if cookies:
        targets += [(f"https://www.facebook.com/search/posts?{urllib.parse.urlencode({'q': t})}", f"search '{t}'", False)
                    for t in cfg.query]

    try:
        with scrape.browser(scrape.browser_cookies(cookies, ".facebook.com") if cookies else None, capture_xhr=GRAPHQL) as b:
            for url, label, filter_by_query in targets:
                try:
                    page = b.fetch(url, page_action=scrape.scroll(scrolls))
                except scrape.ScrapeError as e:
                    errors.append(f"{label}: {e}")
                    continue
                found = parse_stories(scrape.embedded_json(page) + scrape.xhr_json(page)) or parse_page(page, label)
                if not found:
                    errors.append(f"{label}: no posts visible (login wall?)")
                read += len(found)
                posts += [p for p in found if not filter_by_query or matches_query(p.text, cfg.query)]
    except scrape.ScrapeError as e:
        errors.append(str(e))

    return finish([p for p in posts if is_recent(p.created_at, cfg)], errors, read, cfg)


def parse_stories(docs: list) -> list[Post]:
    """Posts from Facebook's own JSON (Story objects inside embedded/GraphQL data)."""
    posts = {}
    for doc in docs:
        for node in scrape.iter_dicts(doc):
            if node.get("__typename") != "Story" or "comet_sections" not in node:
                continue
            text = scrape.first(m["text"] for m in scrape.walk(node, "message") if isinstance(m, dict) and m.get("text"))
            if not text:
                continue
            url = scrape.first((u for u in scrape.walk(node, "url") if isinstance(u, str) and POST_LINK.search(u)), "")
            created = scrape.first(t for t in scrape.walk(node, "creation_time") if isinstance(t, int))
            actor = scrape.first(a["name"] for actors in scrape.walk(node, "actors") if isinstance(actors, list)
                                 for a in actors if isinstance(a, dict) and a.get("name"))
            images = [i["uri"] for i in scrape.walk(node, "photo_image") if isinstance(i, dict) and i.get("uri")]
            key = node.get("post_id") or node.get("id") or url or text
            posts[key] = Post(
                platform="facebook",
                id=str(key),
                url=url or "https://www.facebook.com/",
                text=text,
                author=actor or "",
                created_at=iso(datetime.fromtimestamp(created, timezone.utc)) if created else "",
                likes=_count(node, "reaction_count"),
                comments=_count(node, "total_comment_count", "comment_count"),
                shares=_count(node, "share_count"),
                images=list(dict.fromkeys(images))[:4],
            )
    return list(posts.values())


def _count(node: dict, *keys: str) -> int:
    for key in keys:
        for v in scrape.walk(node, key):
            if isinstance(v, int):
                return v
            if isinstance(v, dict) and isinstance(v.get("count"), int):
                return v["count"]
    return 0


def parse_page(page, default_author: str = "") -> list[Post]:
    posts = []
    for art in scrape.select(page, ARTICLE, "fb-article"):
        # Comments are nested articles; only keep top-level posts.
        if art.find_ancestor(lambda a: a.attrib.get("role") == "article") is not None:
            continue
        msg = scrape.select(art, MESSAGE, "fb-message").first
        text = (msg.get_all_text(separator=" ") if msg is not None else "").strip()
        if not text:
            continue
        url = ""
        for a in art.css("a[href]"):
            href = a.attrib.get("href", "")
            if POST_LINK.search(href):
                url = urllib.parse.urljoin("https://www.facebook.com/", href.split("?__cft__")[0].split("&__cft__")[0])
                break
        author_el = art.css("h2 a, h3 a, strong a").first
        everything = art.get_all_text(separator=" ")
        posts.append(Post(
            platform="facebook",
            id=hashlib.sha1((url or text).encode()).hexdigest()[:16],
            url=url or "https://www.facebook.com/",
            text=text,
            author=(author_el.get_all_text(separator=" ").strip() if author_el is not None else "") or default_author,
            likes=_metric(everything, r"All reactions:\s*([\d.,]+[KMB]?)"),
            comments=_metric(everything, r"([\d.,]+[KMB]?)\s+comments?"),
            shares=_metric(everything, r"([\d.,]+[KMB]?)\s+shares?"),
            images=_images(art),
        ))
    return posts


def _images(art) -> list[str]:
    out = []
    for img in art.css("img[src]"):
        src = img.attrib["src"]
        if ("scontent" in src or "fbcdn" in src) and img.find_ancestor(lambda a: a.tag in ("h2", "h3", "strong")) is None:
            out.append(src)
    return list(dict.fromkeys(out))[:4]


def _metric(text: str, pattern: str) -> int:
    m = re.search(pattern, text, re.I)
    return scrape.parse_count(m.group(1)) if m else 0
