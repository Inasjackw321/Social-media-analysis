"""Find topics in a pile of posts.

1. Pull candidate keyphrases from each post: hashtags, single words, and two-word
   phrases, with stopwords and social-media filler removed.
2. Score each phrase by how many posts use it, weighted by engagement and by how
   many platforms it shows up on.
3. Merge phrases that mostly appear in the same posts into one topic, so
   "climate", "climate change" and "#climatecrisis" end up together.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .models import Post

URL_RE = re.compile(r"https?://\S+|www\.\S+")
MENTION_RE = re.compile(r"@\w+")
WORD_RE = re.compile(r"#?[^\W\d_][\w'’-]*", re.UNICODE)

STOPWORDS = frozenset("""
a about above after again against ago all almost also am an and any are aren't around as at away back
be because been before being below between both but by can can't cannot could couldn't day days did didn't
do does doesn't doing don't down during each even ever every few first for from further get gets getting
go goes going gone got had hadn't has hasn't have haven't having he he'd he'll he's her here here's hers
herself him himself his how how's however i i'd i'll i'm i've if in into is isn't it it's its itself just
know last least less let let's like little look made make makes making many may me might more most much
must my myself need never new next no nor not now of off often oh ok okay on once one only or other ought
our ours ourselves out over own part people per put really right said same say says see seen she she'd
she'll she's should shouldn't show since so some still such sure take than that that's the their theirs
them themselves then there there's these they they'd they'll they're they've thing things think this
those though through time times to today too two under until up upon us use used very via want was
wasn't way we we'd we'll we're we've well went were weren't what what's when when's where where's which
while who who's whom why why's will with won't would wouldn't year years yes yet you you'd you'll you're
you've your yours yourself yourselves
amp rt http https www com video videos watch watching subscribe subscribed channel follow following
followers link bio click comment comments share shared post posts posted live full episode official
check here's join thanks thank please today's tonight week weekend morning everyone anyone someone
something nothing anything gonna wanna lol omg via youtube facebook instagram twitter tweet tweets
means mean breaking update updates latest news story report reports
""".split())

MAX_PHRASES_PER_POST = 60


@dataclass
class Topic:
    label: str
    keywords: list[str]
    post_ids: set[str]
    score: float = 0.0
    platforms: dict[str, int] = field(default_factory=dict)
    engagement: int = 0
    samples: list[dict] = field(default_factory=list)
    is_new: bool = False

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "keywords": self.keywords,
            "score": round(self.score, 2),
            "post_count": len(self.post_ids),
            "engagement": self.engagement,
            "platforms": self.platforms,
            "samples": self.samples,
            "new": self.is_new,
        }


def clean(text: str) -> str:
    return MENTION_RE.sub(" ", URL_RE.sub(" ", text))


def tokenize(text: str) -> list[str]:
    tokens = []
    for raw in WORD_RE.findall(clean(text).lower()):
        tok = raw.strip("'’-").replace("’", "'")
        if tok.endswith("'s"):
            tok = tok[:-2]
        tokens.append(tok)
    return tokens


def _ok(word: str) -> bool:
    return len(word) >= 3 and word not in STOPWORDS


def phrases(text: str) -> set[str]:
    """Candidate keyphrases in one post: hashtags, words, and adjacent word pairs."""
    out: set[str] = set()
    prev: str | None = None
    for tok in tokenize(text):
        if tok.startswith("#"):
            tag = tok[1:]
            if _ok(tag):
                out.add("#" + tag)
            prev = None
            continue
        if _ok(tok):
            out.add(tok)
            if prev:
                out.add(f"{prev} {tok}")
            prev = tok
        else:
            prev = None
        if len(out) >= MAX_PHRASES_PER_POST:
            break
    return out


def _excluded(phrase: str, query: list[str]) -> bool:
    """The search terms themselves aren't interesting as topics."""
    bare = phrase.lstrip("#")
    for q in query:
        q = q.lower().lstrip("#")
        if bare == q or bare == q.replace(" ", ""):
            return True
    return False


def find_topics(
    posts: list[Post],
    query: list[str] | None = None,
    max_topics: int = 15,
    min_posts: int = 2,
    samples_per_topic: int = 5,
) -> list[Topic]:
    query = query or []
    by_id = {f"{p.platform}:{p.id}": p for p in posts}
    weight = {pid: 1.0 + math.log1p(p.engagement) for pid, p in by_id.items()}

    where: dict[str, set[str]] = defaultdict(set)
    for pid, p in by_id.items():
        for ph in phrases(p.text):
            if not _excluded(ph, query):
                where[ph].add(pid)

    # A phrase only counts once from the same author, so one chatty account can't
    # manufacture a "topic".
    candidates = {}
    for ph, pids in where.items():
        authors = {(by_id[i].platform, by_id[i].author or i) for i in pids}
        if len(pids) >= min_posts and len(authors) >= min(2, min_posts):
            candidates[ph] = pids

    # Drop a single word when a two-word phrase containing it covers most of its posts.
    for ph in [c for c in candidates if " " in c]:
        for word in ph.split():
            if word in candidates and len(candidates[ph]) >= 0.6 * len(candidates[word]):
                candidates.pop(word)

    def score(pids: set[str]) -> float:
        n_platforms = len({by_id[i].platform for i in pids})
        return sum(weight[i] for i in pids) * (1 + 0.5 * (n_platforms - 1))

    ranked = sorted(candidates, key=lambda ph: (score(candidates[ph]), " " in ph), reverse=True)

    topics: list[Topic] = []
    for ph in ranked:
        pids = candidates[ph]
        home = next((t for t in topics if _overlap(pids, t.post_ids) >= 0.5), None)
        if home:
            if len(home.keywords) < 8:
                home.keywords.append(ph)
            home.post_ids |= pids
        elif len(topics) < max_topics:
            topics.append(Topic(label=ph, keywords=[ph], post_ids=set(pids)))

    for t in topics:
        members = [by_id[i] for i in t.post_ids]
        t.score = score(t.post_ids)
        t.engagement = sum(p.engagement for p in members)
        t.platforms = dict(Counter(p.platform for p in members).most_common())
        top = sorted(members, key=lambda p: p.engagement, reverse=True)[:samples_per_topic]
        t.samples = [_sample(p) for p in top]

    topics.sort(key=lambda t: t.score, reverse=True)
    return topics


def _overlap(a: set[str], b: set[str]) -> float:
    """Share of the smaller set that's also in the other one."""
    return len(a & b) / min(len(a), len(b)) if a and b else 0.0


def _sample(p: Post) -> dict:
    text = re.sub(r"\s+", " ", p.text).strip()
    return {
        "platform": p.platform,
        "url": p.url,
        "author": p.author,
        "created_at": p.created_at,
        "text": text[:280] + ("…" if len(text) > 280 else ""),
        "engagement": p.engagement,
    }


def top_hashtags(posts: list[Post], n: int = 20) -> list[dict]:
    counts = Counter(h for p in posts for h in p.hashtags)
    return [{"tag": f"#{t}", "count": c} for t, c in counts.most_common(n) if c > 1]
