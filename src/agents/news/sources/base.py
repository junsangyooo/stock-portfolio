"""Common RawItem for all source adapters (L3 output unit)."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional


@dataclass
class RawItem:
    """One collected item before LLM normalization.

    Attributes:
        source_id:  which source it came from (themes.json source id).
        kind:       'rss' | 'arxiv' | 'x'.
        title:      headline / paper title.
        url:        canonical link (dedup key).
        published:  publish timestamp string as given by the feed (may be None).
        summary:    short description / abstract (may be empty).
    """

    source_id: str
    kind: str
    title: str
    url: str
    published: Optional[str] = None
    summary: str = ""
    image: Optional[str] = None  # preview thumbnail URL (RSS media tag), if any

    def dedup_key(self) -> str:
        """Stable key for dedup — url if present else hash(title)."""
        if self.url:
            return self.url.strip()
        return "t:" + hashlib.sha1(self.title.strip().encode("utf-8")).hexdigest()

    def to_record(self, theme: str, lead_lag: str, fetched_at: str) -> dict:
        return {
            "theme": theme,
            "source_id": self.source_id,
            "kind": self.kind,
            "title": self.title,
            "url": self.url,
            "published": self.published,
            "summary": self.summary,
            "image": self.image,        # preview thumbnail (may be None)
            "lead_lag": lead_lag,        # leading | coincident | lagging | backbone
            "fetched_at": fetched_at,
        }
