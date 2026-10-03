"""Command line: python -m social_topics scan --query "ai, elections" """

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .collectors import collect_all
from .config import load_config
from .models import Post, SourceResult
from .report import build_report, load_latest, write_report
from .topics import find_topics


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="social_topics", description="Find topics across social media.")
    sub = ap.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan", help="Collect posts, find topics, write a report for the site.")
    scan.add_argument("--query", default="", help="Comma-separated search terms (default: config.json default_query)")
    scan.add_argument("--platforms", default="", help="Comma-separated: youtube,twitter,facebook,instagram")
    scan.add_argument("--lookback-hours", type=int, default=None)
    scan.add_argument("--config", default=None, help="Path to config.json")
    scan.add_argument("--out", default="site/data", help="Site data directory")
    scan.add_argument("--report-id", default="", help="Report file name (default: timestamp)")
    scan.add_argument("--from-file", default="", help="Analyse posts from a JSON file instead of calling APIs")
    args = ap.parse_args(argv)

    cfg = load_config(args.config, args.query, args.platforms, args.lookback_hours)
    if args.from_file:
        raw = json.loads(Path(args.from_file).read_text())
        posts = [Post(**{k: v for k, v in p.items() if k != "engagement"}) for p in raw]
        sources = {"file": SourceResult("ok", f"Loaded from {args.from_file}", len(posts))}
    else:
        posts, sources = collect_all(cfg)

    for name, r in sources.items():
        print(f"  {name:<10} {r.status:<8} {r.count:>4} posts  {r.message}")

    topics = find_topics(posts, cfg.query, cfg.max_topics, cfg.min_posts_per_topic)
    data_dir = Path(args.out)
    report_id = args.report_id or datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    report = build_report(report_id, cfg, posts, sources, topics, load_latest(data_dir))
    path = write_report(data_dir, report)

    print(f"\n{len(posts)} posts -> {len(topics)} topics. Report: {path}")
    for i, t in enumerate(topics, 1):
        plats = ", ".join(f"{k} {v}" for k, v in t.platforms.items())
        print(f"  {i:>2}. {t.label:<30} {len(t.post_ids):>4} posts  ({plats})")

    if not posts and all(r.status != "ok" for r in sources.values()):
        print("\nNo platform returned data — see the messages above.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
