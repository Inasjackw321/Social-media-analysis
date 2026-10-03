from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ..config import ScanConfig
from ..scrape import ScrapeError


class NotConfigured(Exception):
    """The platform can't be scanned with the credentials/settings available."""


def since(cfg: ScanConfig) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=cfg.lookback_hours)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(value: str) -> datetime | None:
    if not value:
        return None
    value = value.strip().replace("Z", "+00:00")
    # Facebook/Instagram use +0000 without a colon.
    if len(value) > 5 and value[-5] in "+-" and value[-4:].isdigit():
        value = value[:-2] + ":" + value[-2:]
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def normalize_time(value: str) -> str:
    dt = parse_time(value)
    return iso(dt) if dt else ""


def is_recent(value: str, cfg: ScanConfig) -> bool:
    dt = parse_time(value)
    return dt is None or dt >= since(cfg)


def matches_query(text: str, terms: list[str]) -> bool:
    """For feed-style sources that can't search: keep posts mentioning any query term."""
    if not terms:
        return True
    low = text.lower()
    return any(t.lower().lstrip("#") in low for t in terms)


class Found(list):
    """Posts a collector found, plus warnings about the parts that failed."""

    def __init__(self, posts=(), warnings=()):
        super().__init__(posts)
        self.warnings = list(warnings)


def finish(posts: list, errors: list[str], read: int | None = None, cfg: ScanConfig | None = None) -> Found:
    """Fail only if nothing worked; otherwise keep the posts and pass errors on as warnings.

    `read` is how many posts were seen before filtering, so an empty result can
    say "read 80 posts, none matched" instead of looking like a silent failure.
    """
    if errors and not posts:
        raise ScrapeError("; ".join(errors))
    unique = {(p.platform, p.id): p for p in posts}
    notes = list(errors)
    if not unique and read:
        terms = f" mentioning {', '.join(cfg.query)}" if cfg and cfg.query else ""
        hours = f" from the last {cfg.lookback_hours}h" if cfg else ""
        notes.append(f"read {read} posts, none{hours}{terms}")
    return Found(unique.values(), notes)
