"""Shared data types."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

HASHTAG_RE = re.compile(r"#(\w+)", re.UNICODE)


@dataclass
class Post:
    platform: str
    id: str
    url: str
    text: str
    author: str = ""
    created_at: str = ""  # ISO-8601, UTC
    likes: int = 0
    comments: int = 0
    shares: int = 0
    views: int = 0
    hashtags: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.hashtags:
            self.hashtags = sorted({h.lower() for h in HASHTAG_RE.findall(self.text)})

    @property
    def engagement(self) -> int:
        # Views are cheap compared to interactions, so they count for less.
        return self.likes + 2 * self.comments + 3 * self.shares + self.views // 100

    def to_dict(self) -> dict:
        d = asdict(self)
        d["engagement"] = self.engagement
        return d


@dataclass
class SourceResult:
    """What happened when we asked one platform for posts."""

    status: str  # "ok" | "skipped" | "error"
    message: str = ""
    count: int = 0
