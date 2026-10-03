import unittest

from social_topics.models import Post
from social_topics.topics import find_topics, phrases, tokenize


def post(platform, i, text, author=None, likes=0):
    return Post(platform=platform, id=str(i), url=f"https://example.com/{i}", text=text,
                author=author or f"user{i}", likes=likes)


class PhraseTests(unittest.TestCase):
    def test_strips_urls_mentions_and_stopwords(self):
        got = phrases("Watch @bob talk about Solar Panels https://t.co/xyz #GreenEnergy")
        self.assertIn("solar", got)
        self.assertIn("solar panels", got)
        self.assertIn("#greenenergy", got)
        self.assertNotIn("watch", got)
        self.assertNotIn("bob", got)
        self.assertFalse(any("t.co" in p for p in got))

    def test_possessive_is_dropped(self):
        self.assertEqual(tokenize("NASA's rocket"), ["nasa", "rocket"])

    def test_bigrams_do_not_span_stopwords(self):
        self.assertNotIn("rates economy", phrases("rates and the economy"))


class FindTopicsTests(unittest.TestCase):
    def setUp(self):
        self.posts = [
            post("twitter", 1, "The Fed raises interest rates again", likes=500),
            post("youtube", 2, "Why interest rates matter for your mortgage", likes=200),
            post("facebook", 3, "Interest rates hit a 20-year high", likes=50),
            post("instagram", 4, "World Cup final tonight! #WorldCup", likes=900),
            post("twitter", 5, "What a World Cup final #WorldCup", likes=300),
            post("youtube", 6, "My cat sleeping", likes=10),
        ]

    def test_groups_related_posts_across_platforms(self):
        topics = find_topics(self.posts)
        labels = [t.label for t in topics]
        self.assertIn("interest rates", labels)
        rates = topics[labels.index("interest rates")]
        self.assertEqual(rates.post_ids, {"twitter:1", "youtube:2", "facebook:3"})
        self.assertEqual(set(rates.platforms), {"twitter", "youtube", "facebook"})

    def test_merges_synonymous_phrases_into_one_topic(self):
        topics = find_topics(self.posts)
        cup = [t for t in topics if "world cup" in t.keywords or "#worldcup" in t.keywords]
        self.assertEqual(len(cup), 1)
        self.assertGreaterEqual(len(cup[0].keywords), 2)

    def test_one_off_posts_are_not_topics(self):
        self.assertFalse(any("cat" in t.keywords for t in find_topics(self.posts)))

    def test_query_terms_are_excluded(self):
        topics = find_topics(self.posts, query=["interest rates"])
        self.assertFalse(any(t.label == "interest rates" for t in topics))

    def test_single_author_cannot_make_a_topic(self):
        spam = [post("twitter", i, "Buy crypto coins now", author="spammer") for i in range(10)]
        self.assertEqual(find_topics(spam), [])

    def test_samples_sorted_by_engagement(self):
        rates = next(t for t in find_topics(self.posts) if t.label == "interest rates")
        self.assertEqual([s["url"] for s in rates.samples][0], "https://example.com/1")

    def test_max_topics(self):
        self.assertLessEqual(len(find_topics(self.posts, max_topics=1)), 1)


if __name__ == "__main__":
    unittest.main()
