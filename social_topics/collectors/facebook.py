"""Facebook, scraped with Scrapling's stealth browser.

Facebook only renders posts with JavaScript, so this opens each Page from
config.json in a headless stealth Chromium, scrolls to load posts, and reads
them off the page.

- Without login, Facebook shows a limited number of posts from public Pages
  (and sometimes a login wall instead).
- With the FACEBOOK_COOKIES secret ("c_user=...; xs=..." from a logged-in
  browser), it sees full Page feeds and can also search posts by keyword.
"""

from __future__ import annotations

import hashlib
import re
import urllib.parse

from .. import scrape
from ..config import ScanConfig
from ..models import Post
from .base import NotConfigured, finish, matches_query

ARTICLE = 'div[role="article"]'
MESSAGE = 'div[data-ad-preview="message"], div[data-ad-comet-preview="message"]'
POST_LINK = re.compile(r"/(posts|videos|reel|photos|permalink\.php|story\.php)[/?]|story_fbid=|/p/")


def collect(cfg: ScanConfig) -> list[Post]:
    pages = cfg.facebook.get("pages", [])
    cookies = scrape.cookie_header_to_dict(cfg.secret("FACEBOOK_COOKIES"))
    if not pages and not (cookies and cfg.query):
        raise NotConfigured("List Pages under facebook.pages in config.json, or set FACEBOOK_COOKIES to search by keyword.")

    scrolls = int(cfg.facebook.get("scrolls", 5))
    posts: list[Post] = []
    errors: list[str] = []
    targets = [(f"https://www.facebook.com/{urllib.parse.quote(p)}", p, True) for p in pages]
    if cookies:
        targets += [(f"https://www.facebook.com/search/posts?{urllib.parse.urlencode({'q': t})}", f"search '{t}'", False)
                    for t in cfg.query]

    for url, label, filter_by_query in targets:
        try:
            page = scrape.browse(
                url,
                cookies=scrape.browser_cookies(cookies, ".facebook.com") if cookies else None,
                page_action=scrape.scroll(scrolls),
            )
            found = parse_page(page, default_author=label)
        except scrape.ScrapeError as e:
            errors.append(f"{label}: {e}")
            continue
        if not found:
            errors.append(f"{label}: no posts visible (login wall?)")
        posts += [p for p in found if not filter_by_query or matches_query(p.text, cfg.query)]

    return finish(posts, errors)


def parse_page(page, default_author: str = "") -> list[Post]:
    posts = []
    for art in page.css(ARTICLE):
        # Comments are nested articles; only keep top-level posts.
        if art.find_ancestor(lambda a: a.attrib.get("role") == "article") is not None:
            continue
        msg = art.css(MESSAGE).first
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
        ))
    return posts


def _metric(text: str, pattern: str) -> int:
    m = re.search(pattern, text, re.I)
    return scrape.parse_count(m.group(1)) if m else 0
