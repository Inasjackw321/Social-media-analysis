import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest import mock

from scrapling.parser import Selector

from social_topics import scrape
from social_topics.collectors import collect_all, facebook, instagram, twitter, youtube
from social_topics.collectors.base import NotConfigured
from social_topics.config import load_config

NOW = datetime.now(timezone.utc)
RECENT = (NOW - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S+00:00")
OLD = (NOW - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def cfg(env=None, query=None, platforms=None, **sections):
    c = load_config(path="/nonexistent.json", env=env or {}, query=query, platforms=platforms)
    for k, v in sections.items():
        setattr(c, k, v)
    return c


def page(body: str | bytes):
    """Something shaped like a Scrapling response for the code under test."""
    raw = body.encode() if isinstance(body, str) else body
    return SimpleNamespace(body=raw, status=200)


class HelperTests(unittest.TestCase):
    def test_parse_count(self):
        self.assertEqual(scrape.parse_count("1.2K views"), 1200)
        self.assertEqual(scrape.parse_count("3,456 views"), 3456)
        self.assertEqual(scrape.parse_count("2M"), 2_000_000)
        self.assertEqual(scrape.parse_count("No views"), 0)

    def test_relative_time(self):
        got = scrape.relative_time("Streamed 3 hours ago", now=datetime(2026, 1, 1, 12, tzinfo=timezone.utc))
        self.assertEqual(got, "2026-01-01T09:00:00Z")
        self.assertEqual(scrape.relative_time("yesterday"), "")

    def test_extract_json_after(self):
        html = '<script>var ytInitialData = {"a": {"b": "};"}};</script>'
        self.assertEqual(scrape.extract_json_after(html, "var ytInitialData = "), {"a": {"b": "};"}})

    def test_cookie_header(self):
        self.assertEqual(scrape.cookie_header_to_dict("c_user=1; xs=abc=="), {"c_user": "1", "xs": "abc=="})


YT_SEARCH = "<html><script>var ytInitialData = " + json.dumps({"contents": {"x": [
    {"videoRenderer": {
        "videoId": "v1", "title": {"runs": [{"text": "Storm hits the coast"}]},
        "ownerText": {"runs": [{"text": "News Ch"}]}, "viewCountText": {"simpleText": "12,345 views"},
        "publishedTimeText": {"simpleText": "2 hours ago"},
        "detailedMetadataSnippets": [{"snippetText": {"runs": [{"text": "Live "}, {"text": "#storm"}]}}]}},
    {"videoRenderer": {"videoId": "v2", "title": {"runs": [{"text": "Old"}]},
                       "publishedTimeText": {"simpleText": "3 weeks ago"}}},
]}}) + ";</script></html>"

RSS = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
 <entry>
  <yt:videoId>abc123</yt:videoId><title>Storm coverage</title>
  <author><name>News Channel</name></author><published>{RECENT}</published>
  <media:group><media:description>Live coverage</media:description>
   <media:community><media:starRating count="120"/><media:statistics views="5000"/></media:community>
  </media:group>
 </entry>
 <entry><yt:videoId>old1</yt:videoId><title>Old storm</title><published>{OLD}</published></entry>
</feed>"""


class YouTubeTests(unittest.TestCase):
    def test_search_page(self):
        with mock.patch.object(scrape, "get", return_value=page(YT_SEARCH)) as get:
            posts = youtube.collect(cfg(query="storm", youtube={}))
        self.assertEqual(get.call_args.args[1]["sp"], "EgIIAg==")  # uploaded today
        self.assertEqual([p.id for p in posts], ["v1"])  # 3-week-old video dropped
        p = posts[0]
        self.assertEqual((p.author, p.views, p.hashtags), ("News Ch", 12345, ["storm"]))

    def test_channel_rss(self):
        with mock.patch.object(scrape, "get", return_value=page(RSS)):
            posts = youtube.collect(cfg(youtube={"channels": ["UC1"]}))
        self.assertEqual([(p.id, p.views, p.likes) for p in posts], [("abc123", 5000, 120)])

    def test_partial_failure_is_a_warning(self):
        def fake_get(url, *a, **k):
            if "feeds" in url:
                raise scrape.ScrapeError("HTTP 404")
            return page(YT_SEARCH)

        with mock.patch.object(scrape, "get", side_effect=fake_get):
            posts = youtube.collect(cfg(query="storm", youtube={"channels": ["UCbad"]}))
        self.assertEqual(len(posts), 1)
        self.assertIn("UCbad", posts.warnings[0])

    def test_not_configured(self):
        with self.assertRaises(NotConfigured):
            youtube.collect(cfg(youtube={}))


SYNDICATION = '<html><script id="__NEXT_DATA__" type="application/json">' + json.dumps({"props": {"pageProps": {
    "timeline": {"entries": [{"type": "tweet", "content": {"tweet": {
        "id_str": "111", "full_text": "Breaking: storm makes landfall #storm",
        "created_at": NOW.strftime("%a %b %d %H:%M:%S +0000 %Y"),
        "favorite_count": 40, "retweet_count": 5, "quote_count": 1, "reply_count": 3,
        "user": {"screen_name": "BBCBreaking"}}}}]}}}}) + "</script></html>"

X_SEARCH = """<html><body>
<article data-testid="tweet">
  <a href="/alice/status/222"><time datetime="2026-10-03T09:00:00.000Z">1h</time></a>
  <div data-testid="tweetText"><span>Storm </span><a>#update</a></div>
  <div role="group" aria-label="5 replies, 20 reposts, 1,204 likes, 3 bookmarks, 50K views"></div>
</article>
<article data-testid="tweet"><div>promoted, no link</div></article>
</body></html>"""


class TwitterTests(unittest.TestCase):
    def test_accounts_via_embed_timeline(self):
        with mock.patch.object(scrape, "get", return_value=page(SYNDICATION)) as get:
            posts = twitter.collect(cfg(twitter={"accounts": ["BBCBreaking"]}))
        self.assertIn("screen-name/BBCBreaking", get.call_args.args[0])
        p = posts[0]
        self.assertEqual((p.url, p.likes, p.shares, p.comments), ("https://x.com/BBCBreaking/status/111", 40, 6, 3))

    def test_search_page_parsing(self):
        posts = twitter.parse_search_page(Selector(X_SEARCH))
        self.assertEqual(len(posts), 1)
        p = posts[0]
        self.assertEqual((p.author, p.id, p.created_at), ("@alice", "222", "2026-10-03T09:00:00Z"))
        self.assertEqual((p.comments, p.shares, p.likes, p.views), (5, 20, 1204, 50000))
        self.assertIn("#update", p.text)

    def test_search_uses_login_cookie(self):
        with mock.patch.object(scrape, "browse", return_value=Selector(X_SEARCH)) as browse:
            twitter.collect(cfg(env={"X_AUTH_TOKEN": "tok"}, query="storm", twitter={}))
        self.assertEqual(browse.call_args.kwargs["cookies"][0]["name"], "auth_token")
        self.assertIn("x.com/search", browse.call_args.args[0])

    def test_search_without_login_is_skipped(self):
        with self.assertRaises(NotConfigured):
            twitter.collect(cfg(query="storm", twitter={}))


IG_PROFILE = {"data": {"user": {"username": "natgeo", "edge_owner_to_timeline_media": {"edges": [
    {"node": {"id": "1", "shortcode": "AbC", "taken_at_timestamp": int(NOW.timestamp()) - 60,
              "edge_media_to_caption": {"edges": [{"node": {"text": "Volcano erupts #volcano"}}]},
              "edge_liked_by": {"count": 900}, "edge_media_to_comment": {"count": 12}}},
]}}}}
IG_TAG = {"data": {"top": {"sections": [{"layout_content": {"medias": [{"media": {
    "pk": "9", "code": "XyZ", "taken_at": int(NOW.timestamp()) - 60, "caption": {"text": "Goal! #worldcup"},
    "like_count": 10, "comment_count": 2, "user": {"username": "fan"}}}]}}]}}}


class InstagramTests(unittest.TestCase):
    def test_profile(self):
        with mock.patch.object(scrape, "get_json", return_value=IG_PROFILE) as get:
            posts = instagram.collect(cfg(instagram={"accounts": ["natgeo"]}))
        self.assertEqual(get.call_args.args[1], {"username": "natgeo"})
        p = posts[0]
        self.assertEqual((p.url, p.likes, p.comments, p.author), ("https://www.instagram.com/p/AbC/", 900, 12, "@natgeo"))

    def test_hashtags_need_session(self):
        with mock.patch.object(scrape, "get_json", return_value=IG_TAG) as get:
            posts = instagram.collect(cfg(env={"INSTAGRAM_SESSIONID": "s"}, query="World Cup", instagram={}))
        self.assertEqual(get.call_args.args[1], {"tag_name": "worldcup"})
        self.assertEqual(get.call_args.args[3], {"sessionid": "s"})
        self.assertEqual(posts[0].author, "@fan")


FB_PAGE = """<html><body>
<div role="article">
  <h2><a href="/bbcnews">BBC News</a></h2>
  <a href="https://www.facebook.com/bbcnews/posts/pfbid0abc?__cft__[0]=zz">2h</a>
  <div data-ad-preview="message">Floods hit the valley</div>
  <span>All reactions: 1.5K</span><span>230 comments</span><span>45 shares</span>
  <div role="article"><div data-ad-preview="message">a nested comment</div></div>
</div>
<div role="article"><div>photo with no caption</div></div>
</body></html>"""


class FacebookTests(unittest.TestCase):
    def test_page_parsing(self):
        posts = facebook.parse_page(Selector(FB_PAGE))
        self.assertEqual(len(posts), 1)
        p = posts[0]
        self.assertEqual(p.url, "https://www.facebook.com/bbcnews/posts/pfbid0abc")
        self.assertEqual((p.author, p.likes, p.comments, p.shares), ("BBC News", 1500, 230, 45))

    def test_cookies_enable_search(self):
        with mock.patch.object(scrape, "browse", return_value=Selector(FB_PAGE)) as browse:
            facebook.collect(cfg(env={"FACEBOOK_COOKIES": "c_user=1; xs=2"}, query="floods", facebook={}))
        self.assertIn("search/posts?q=floods", browse.call_args.args[0])
        self.assertEqual({c["name"] for c in browse.call_args.kwargs["cookies"]}, {"c_user", "xs"})

    def test_login_wall_reported(self):
        with mock.patch.object(scrape, "browse", return_value=Selector("<html>Log in</html>")):
            with self.assertRaisesRegex(scrape.ScrapeError, "login wall"):
                facebook.collect(cfg(facebook={"pages": ["bbcnews"]}))


class CollectAllTests(unittest.TestCase):
    def test_failures_are_isolated(self):
        def boom(_):
            raise scrape.ScrapeError("HTTP 500")

        def skip(_):
            raise NotConfigured("nothing to scan")

        c = cfg(platforms="youtube,twitter,facebook")
        posts, results = collect_all(c, {"youtube": skip, "twitter": boom, "facebook": lambda _: []})
        self.assertEqual(posts, [])
        self.assertEqual({k: v.status for k, v in results.items()},
                         {"youtube": "skipped", "twitter": "error", "facebook": "ok"})


if __name__ == "__main__":
    unittest.main()
