"""Turn a scan into JSON files the GitHub Pages site reads.

site/data/index.json            list of every scan, newest first
site/data/reports/<id>.json     one full report per scan
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .config import ScanConfig
from .models import Post, SourceResult
from .topics import Topic, top_hashtags

INDEX_LIMIT = 200
GALLERY_LIMIT = 120


POSTS_LIMIT = 200


def newest_first(posts: list[Post]) -> list[Post]:
    return sorted(posts, key=lambda p: (bool(p.created_at), p.created_at, p.engagement), reverse=True)


def _brief(p: Post, chars: int) -> dict:
    return {
        "platform": p.platform,
        "url": p.url,
        "author": p.author,
        "created_at": p.created_at,
        "text": " ".join(p.text.split())[:chars],
        "images": p.images[:4],
        "engagement": p.engagement,
    }


def gallery(posts: list[Post]) -> list[dict]:
    """Every post that has a picture, newest first (undated posts last)."""
    return [_brief(p, 160) for p in newest_first([p for p in posts if p.images])[:GALLERY_LIMIT]]


def build_report(
    report_id: str,
    cfg: ScanConfig,
    posts: list[Post],
    sources: dict[str, SourceResult],
    topics: list[Topic],
    previous: dict | None = None,
) -> dict:
    if previous:
        seen = {k for t in previous.get("topics", []) for k in t.get("keywords", [])}
        for t in topics:
            t.is_new = not any(k in seen for k in t.keywords)
    return {
        "id": report_id,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "query": cfg.query,
        "lookback_hours": cfg.lookback_hours,
        "sources": {name: vars(r) for name, r in sources.items()},
        "totals": {
            "posts": len(posts),
            "by_platform": dict(Counter(p.platform for p in posts)),
            "engagement": sum(p.engagement for p in posts),
        },
        "topics": [t.to_dict() for t in topics],
        "hashtags": top_hashtags(posts),
        "gallery": gallery(posts),
        "posts": [_brief(p, 400) for p in newest_first(posts)[:POSTS_LIMIT]],
    }


def load_index(data_dir: Path) -> dict:
    path = data_dir / "index.json"
    return json.loads(path.read_text()) if path.exists() else {"reports": []}


def load_latest(data_dir: Path) -> dict | None:
    reports = load_index(data_dir)["reports"]
    if not reports:
        return None
    path = data_dir / "reports" / f"{reports[0]['id']}.json"
    return json.loads(path.read_text()) if path.exists() else None


def write_report(data_dir: Path, report: dict) -> Path:
    reports_dir = data_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{report['id']}.json"
    path.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n")

    index = load_index(data_dir)
    entries = [r for r in index["reports"] if r["id"] != report["id"]]
    entries.insert(0, {
        "id": report["id"],
        "generated_at": report["generated_at"],
        "query": report["query"],
        "posts": report["totals"]["posts"],
        "topics": len(report["topics"]),
        "top_topics": [t["label"] for t in report["topics"][:3]],
    })
    entries.sort(key=lambda r: r["generated_at"], reverse=True)
    index["reports"] = entries[:INDEX_LIMIT]
    (data_dir / "index.json").write_text(json.dumps(index, indent=1, ensure_ascii=False) + "\n")
    return path
