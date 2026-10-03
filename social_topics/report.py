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
        "images": len(report.get("gallery", [])),
    })
    entries.sort(key=lambda r: r["generated_at"], reverse=True)
    index["reports"] = entries[:INDEX_LIMIT]
    (data_dir / "index.json").write_text(json.dumps(index, indent=1, ensure_ascii=False) + "\n")
    return path


PLATFORM_NAMES = {"youtube": "YouTube", "twitter": "X", "facebook": "Facebook", "instagram": "Instagram", "file": "File"}


def _md(text: str) -> str:
    """Make scraped text safe to drop into Markdown/HTML on GitHub."""
    text = " ".join((text or "").split())
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    for ch in "\\`*_[]|#":
        text = text.replace(ch, "\\" + ch)
    return text.replace("@", "@​")  # don't turn handles into GitHub @mentions


def _safe_url(url: str) -> str:
    return url.replace(" ", "%20").replace(")", "%29").replace('"', "%22") if url.startswith("https://") else ""


def to_markdown(report: dict) -> str:
    q = ", ".join(report["query"]) or "general (no search terms)"
    lines = [
        f"# Scan: {_md(q)}",
        "",
        f"Scanned **{report['generated_at'].replace('T', ' ').replace('Z', ' UTC')}**, covering the last "
        f"{report['lookback_hours']} hours. {report['totals']['posts']} matching posts found.",
        "",
        "| Platform | Result |",
        "|---|---|",
    ]
    for name, s in report["sources"].items():
        detail = f"{s['count']} posts" if s["status"] == "ok" else s["status"]
        if s.get("message"):
            detail += f" ({_md(s['message'][:300])})"
        lines.append(f"| {PLATFORM_NAMES.get(name, name)} | {detail} |")

    gallery = report.get("gallery", [])
    lines += ["", f"## Latest images ({len(gallery)})", ""]
    if gallery:
        lines.append("<table>")
        for i in range(0, len(gallery), 3):
            lines.append("<tr>")
            for g in gallery[i:i + 3]:
                img, url = _safe_url(g["images"][0]), _safe_url(g["url"])
                when = (g["created_at"] or "date unknown").replace("T", " ").replace("Z", "")
                lines.append(
                    f'<td width="33%" valign="top"><a href="{url}"><img src="{img}" width="240"></a><br>'
                    f"<sub>{PLATFORM_NAMES.get(g['platform'], g['platform'])} · {_md(g['author'])} · {when}</sub><br>"
                    f"<sub>{_md(g['text'][:120])}</sub></td>"
                )
            lines.append("</tr>")
        lines.append("</table>")
    else:
        lines.append("_No posts with images matched._")

    posts = report.get("posts", [])
    lines += ["", f"## Matching posts, newest first ({len(posts)})", ""]
    for p in posts:
        when = (p["created_at"] or "date unknown").replace("T", " ").replace("Z", "")
        link = f"[open]({_safe_url(p['url'])})" if _safe_url(p["url"]) else ""
        lines.append(f"- **{PLATFORM_NAMES.get(p['platform'], p['platform'])}** · {_md(p['author'])} · {when} · {link}  ")
        lines.append(f"  {_md(p['text'][:300])}")
    if not posts:
        lines.append("_None._")

    if report["topics"]:
        lines += ["", "## Topics", "", "| # | Topic | Posts | Platforms |", "|---|---|---|---|"]
        for i, t in enumerate(report["topics"], 1):
            plats = ", ".join(f"{PLATFORM_NAMES.get(k, k)} {v}" for k, v in t["platforms"].items())
            lines.append(f"| {i} | {_md(t['label'])}{' 🆕' if t['new'] else ''} | {t['post_count']} | {plats} |")
    return "\n".join(lines) + "\n"


def write_markdown(results_dir: Path, report: dict, data_dir: Path) -> Path:
    """results/<id>.md for this scan, results/latest.md, and results/README.md listing them all."""
    results_dir.mkdir(parents=True, exist_ok=True)
    md = to_markdown(report)
    path = results_dir / f"{report['id']}.md"
    path.write_text(md)
    (results_dir / "latest.md").write_text(md)
    rows = ["# Scan results", "", "Newest first. Each scan is also on the GitHub Pages site.", "",
            "| When | Search | Posts | Images | Report |", "|---|---|---|---|---|"]
    for r in load_index(data_dir)["reports"]:
        if not (results_dir / f"{r['id']}.md").exists():
            continue
        q = ", ".join(r["query"]) or "general"
        rows.append(f"| {r['generated_at'].replace('T', ' ').replace('Z', '')} | {_md(q)} | {r['posts']} | "
                    f"{r.get('images', '')} | [{r['id']}]({r['id']}.md) |")
    (results_dir / "README.md").write_text("\n".join(rows) + "\n")
    return path
