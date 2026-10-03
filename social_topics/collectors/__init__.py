from __future__ import annotations

from ..config import ScanConfig
from ..models import Post, SourceResult
from . import facebook, instagram, twitter, youtube
from .base import NotConfigured

COLLECTORS = {
    "youtube": youtube.collect,
    "twitter": twitter.collect,
    "facebook": facebook.collect,
    "instagram": instagram.collect,
}


def collect_all(cfg: ScanConfig, collectors: dict | None = None) -> tuple[list[Post], dict[str, SourceResult]]:
    """Run every selected collector. One platform failing never stops the others."""
    collectors = collectors or COLLECTORS
    posts: list[Post] = []
    results: dict[str, SourceResult] = {}
    for name in cfg.platforms:
        try:
            found = collectors[name](cfg)
        except NotConfigured as e:
            results[name] = SourceResult("skipped", str(e))
        except Exception as e:  # noqa: BLE001 - report any failure, keep scanning
            results[name] = SourceResult("error", f"{type(e).__name__}: {e}"[:500])
        else:
            posts += found
            warnings = "; ".join(getattr(found, "warnings", []))
            results[name] = SourceResult("ok", warnings[:500], len(found))
    return posts, results
