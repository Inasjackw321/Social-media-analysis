"""Scan settings: config.json for what to watch, environment variables for secrets."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

ALL_PLATFORMS = ("youtube", "twitter", "facebook", "instagram")
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"


@dataclass
class ScanConfig:
    query: list[str] = field(default_factory=list)
    platforms: list[str] = field(default_factory=lambda: list(ALL_PLATFORMS))
    lookback_hours: int = 24
    max_topics: int = 15
    min_posts_per_topic: int = 2
    youtube: dict = field(default_factory=dict)
    twitter: dict = field(default_factory=dict)
    facebook: dict = field(default_factory=dict)
    instagram: dict = field(default_factory=dict)
    env: dict = field(default_factory=lambda: dict(os.environ))

    def secret(self, name: str) -> str:
        return (self.env.get(name) or "").strip()


def parse_list(value: str | list | None) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        value = value.split(",")
    return [v.strip() for v in value if v and v.strip()]


def load_config(
    path: Path | str | None = None,
    query: str | list | None = None,
    platforms: str | list | None = None,
    lookback_hours: int | None = None,
    env: dict | None = None,
) -> ScanConfig:
    path = Path(path) if path else DEFAULT_CONFIG_PATH
    raw = json.loads(path.read_text()) if path.exists() else {}

    chosen = parse_list(platforms) or parse_list(raw.get("platforms")) or list(ALL_PLATFORMS)
    unknown = [p for p in chosen if p not in ALL_PLATFORMS]
    if unknown:
        raise ValueError(f"Unknown platform(s): {', '.join(unknown)}. Choose from {', '.join(ALL_PLATFORMS)}.")

    cfg = ScanConfig(
        query=parse_list(query) or parse_list(raw.get("default_query")),
        platforms=chosen,
        lookback_hours=int(lookback_hours or raw.get("lookback_hours") or 24),
        max_topics=int(raw.get("max_topics", 15)),
        min_posts_per_topic=int(raw.get("min_posts_per_topic", 2)),
        youtube=raw.get("youtube", {}),
        twitter=raw.get("twitter", {}),
        facebook=raw.get("facebook", {}),
        instagram=raw.get("instagram", {}),
    )
    if env is not None:
        cfg.env = env
    return cfg
