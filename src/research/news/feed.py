"""Landing-page qualitative feed.

The query moved to read.py when the corpus moved from per-theme jsonl into
news.db — every read of that book lives in one module. This stays as the name
the routers and the quant tools already import.
"""
from __future__ import annotations

from .read import feed as _feed, published_iso  # noqa: F401  (re-exported)


def build(limit: int = 60) -> dict:
    """Assemble the landing feed. See read.feed."""
    return _feed(limit=limit)
