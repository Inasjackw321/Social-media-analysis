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


class FakeBrowser:
    """Stands in for scrape.browser(): records calls and serves canned pages."""

    def __init__(self, pages):
        self.pages = pages  # url substring -> page (or exception)
        self.urls, self.cookies, self.capture_xhr = [], None, None

    def __call__(self, cookies=None, capture_xhr=None):
        self.cookies, self.capture_xhr = cookies, capture_xhr
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def fetch(self, url, page_action=None, wait_selector=None):
        self.urls.append(url)
        result = next((v for k, v in self.pages.items() if k in url), Selector("<html></html>"))
        if isinstance(result, Exception):
            raise result
        return result


def with_xhr(html, *docs):
    """A parsed page plus captured background JSON, like a Scrapling browser response."""
    sel = Selector(html)
    sel.captured_xhr = [SimpleNamespace(body=json.dumps(d).encode()) for d in docs]
    return sel


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

    def test_json_documents(self):
        self.assertEqual(scrape.json_documents('{"a":1}\n{"b":2}'), [{"a": 1}, {"b": 2}])
        self.assertEqual(scrape.json_documents("not json"), [])

    def test_select_is_adaptive_when_page_supports_it(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            conf = {"adaptive": True, "storage_args": {"storage_file": os.path.join(d, "a.db"), "url": "https://fb.com/x"}}
            old = Selector('<div role="feed"><div role="article"><div data-ad-preview="message" dir="auto">Hi</div></div></div>', **conf)
            self.assertEqual(len(scrape.select(old, 'div[data-ad-preview="message"]', "msg")), 1)
            new = Selector('<div role="feed"><div role="article"><div data-ad-comet-preview="message" dir="auto">Hi</div></div></div>', **conf)
            self.assertEqual([e.text for e in scrape.select(new, 'div[data-ad-preview="message"]', "msg")], ["Hi"])

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
        "user": {"screen_name": "BBCBreaking"},
        "mediaDetails": [{"media_url_https": "https://pbs.twimg.com/media/A.jpg"}],
        "entities": {"media": [{"media_url_https": "https://pbs.twimg.com/media/A.jpg"}]}}}}]}}}}) + "</script></html>"

X_SEARCH = """<html><body>
<article data-testid="tweet">
  <a href="/alice/status/222"><time datetime="2026-10-03T09:00:00.000Z">1h</time></a>
  <div data-testid="tweetText"><span>Storm </span><a>#update</a></div>
  <div data-testid="tweetPhoto"><img src="https://pbs.twimg.com/media/B.jpg"></div>
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
        self.assertEqual(p.images, ["https://pbs.twimg.com/media/A.jpg"])  # de-duplicated

    def test_search_page_parsing(self):
        posts = twitter.parse_search_page(Selector(X_SEARCH))
        self.assertEqual(len(posts), 1)
        p = posts[0]
        self.assertEqual((p.author, p.id, p.created_at), ("@alice", "222", "2026-10-03T09:00:00Z"))
        self.assertEqual((p.comments, p.shares, p.likes, p.views), (5, 20, 1204, 50000))
        self.assertIn("#update", p.text)
        self.assertEqual(p.images, ["https://pbs.twimg.com/media/B.jpg"])

    def test_search_uses_login_cookie(self):
        fake = FakeBrowser({"x.com/search": Selector(X_SEARCH)})
        with mock.patch.object(scrape, "browser", fake):
            twitter.collect(cfg(env={"X_AUTH_TOKEN": "tok"}, query="storm", twitter={}))
        self.assertEqual(fake.cookies[0]["name"], "auth_token")
        self.assertIn("f=live", fake.urls[0])  # newest first
        self.assertIn("SearchTimeline", fake.capture_xhr)

    def test_graphql_capture_when_embed_is_empty(self):
        gql = {"data": {"user": {"result": {"timeline": {"instructions": [{"entries": [{"content": {"itemContent": {
            "tweet_results": {"result": {
                "__typename": "Tweet",
                "core": {"user_results": {"result": {"legacy": {"screen_name": "AJEnglish"}}}},
                "views": {"count": "9000"},
                "legacy": {"id_str": "333", "full_text": "Scenes from Sanaa today",
                           "created_at": NOW.strftime("%a %b %d %H:%M:%S +0000 %Y"),
                           "favorite_count": 7, "retweet_count": 2, "quote_count": 0, "reply_count": 1,
                           "extended_entities": {"media": [{"media_url_https": "https://pbs.twimg.com/media/S.jpg"}]}},
            }}}}}]}]}}}}}
        fake = FakeBrowser({"x.com/AJEnglish": with_xhr("<html></html>", gql)})
        with mock.patch.object(scrape, "get", side_effect=scrape.ScrapeError("HTTP 429")), \
             mock.patch.object(scrape, "browser", fake):
            posts = twitter.collect(cfg(query="sanaa", twitter={"accounts": ["AJEnglish"]}))
        self.assertEqual(fake.urls, ["https://x.com/AJEnglish"])
        p = posts[0]
        self.assertEqual((p.url, p.views, p.images), ("https://x.com/AJEnglish/status/333", 9000,
                                                       ["https://pbs.twimg.com/media/S.jpg"]))

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
  <h2><a href="/bbcnews"><img src="https://scontent.xx.fbcdn.net/avatar.jpg">BBC News</a></h2>
  <img src="https://scontent.xx.fbcdn.net/photo.jpg"><img src="https://static.xx.fbcdn.net/emoji.png">
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
        self.assertIn("https://scontent.xx.fbcdn.net/photo.jpg", p.images)
        self.assertNotIn("https://scontent.xx.fbcdn.net/avatar.jpg", p.images)

    def test_cookies_enable_search(self):
        fake = FakeBrowser({"search/posts": Selector(FB_PAGE)})
        with mock.patch.object(scrape, "browser", fake):
            facebook.collect(cfg(env={"FACEBOOK_COOKIES": "c_user=1; xs=2"}, query="floods", facebook={}))
        self.assertIn("search/posts?q=floods", fake.urls[0])
        self.assertEqual({c["name"] for c in fake.cookies}, {"c_user", "xs"})

    def test_json_stories_preferred(self):
        story = {"__typename": "Story", "post_id": "77", "comet_sections": {
            "content": {"story": {"message": {"text": "Photos: life in Sanaa"}}},
            "context_layout": {"story": {"comet_sections": {"metadata": [{"story": {"creation_time": int(NOW.timestamp()) - 600}}]}}},
        }, "url": "https://www.facebook.com/aljazeera/posts/pfbid077",
            "actors": [{"name": "Al Jazeera"}],
            "attachments": [{"styles": {"attachment": {"media": {"photo_image": {"uri": "https://scontent.xx.fbcdn.net/s.jpg"}}}}}],
            "feedback": {"reaction_count": {"count": 321}, "share_count": {"count": 12}}}
        embedded = f'<html><script type="application/json">{json.dumps({"require": [story]})}</script></html>'
        fake = FakeBrowser({"aljazeera": with_xhr(embedded)})
        with mock.patch.object(scrape, "browser", fake):
            posts = facebook.collect(cfg(query="sanaa", facebook={"pages": ["aljazeera"]}))
        self.assertEqual(fake.capture_xhr, facebook.GRAPHQL)
        p = posts[0]
        self.assertEqual((p.author, p.likes, p.shares), ("Al Jazeera", 321, 12))
        self.assertTrue(p.created_at)  # dated, unlike HTML-only parsing
        self.assertEqual(p.images, ["https://scontent.xx.fbcdn.net/s.jpg"])

    def test_video_thumbnails_count_as_images(self):
        story = {"__typename": "Story", "post_id": "5", "comet_sections": {}, "creation_time": int(NOW.timestamp()),
                 "message": {"text": "Live from Sanaa"}, "url": "https://www.facebook.com/AlArabiya/videos/5/",
                 "attachments": [{"media": {"__typename": "Video",
                                            "preferred_thumbnail": {"image": {"uri": "https://scontent.xx.fbcdn.net/v.jpg"}}}}]}
        self.assertEqual(facebook.parse_stories([story])[0].images, ["https://scontent.xx.fbcdn.net/v.jpg"])

    def test_explains_when_nothing_matched(self):
        fake = FakeBrowser({"bbcnews": Selector(FB_PAGE)})
        with mock.patch.object(scrape, "browser", fake):
            posts = facebook.collect(cfg(query="sanaa", facebook={"pages": ["bbcnews"]}))
        self.assertEqual(list(posts), [])
        self.assertIn("read 1 posts, none from the last 24h mentioning sanaa", posts.warnings[0])

    def test_old_json_stories_are_dropped(self):
        story = {"__typename": "Story", "post_id": "1", "comet_sections": {}, "creation_time": 1_000_000_000,
                 "message": {"text": "Sanaa in 2001"}}
        fake = FakeBrowser({"aljazeera": with_xhr("<html></html>", {"data": story})})
        with mock.patch.object(scrape, "browser", fake):
            self.assertEqual(facebook.collect(cfg(facebook={"pages": ["aljazeera"]})), [])

    def test_login_wall_reported(self):
        with mock.patch.object(scrape, "browser", FakeBrowser({"bbcnews": Selector("<html>Log in</html>")})):
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
