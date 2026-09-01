"""Config loaders for the qualitative pipeline: themes and sources.

``themes.json`` defines the fields we reason about — keywords, related-edges,
definitions. ``sources.json`` is a flat list of feeds. The two are deliberately
NOT linked: collection sweeps every source once and themes are attached to
individual articles, so a feed that happens to cover three fields is fetched
once and stored once instead of three times under three theme folders.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_CATALOG_PATH = Path(__file__).resolve().parent / "themes.json"
_SOURCES_PATH = Path(__file__).resolve().parent / "sources.json"


@dataclass
class Theme:
    """One theme entry from themes.json.

    Attributes:
        theme_id:   stable id, e.g. "battery".
        name_kr:    Korean display name.
        always_on:  injected into every stock evaluation (macro backbone).
        definition: one-line scope description.
        keywords:   {"ko": [...], "en": [...]} — also used for ETF name rules.
        related:    list of {theme, relation, weight} edges.

    The old ``active``/``shadow`` flags are gone. They encoded "we do not
    collect for this field" and "this field has no holder", and collection is
    now neither source-bound nor holdings-bound: every theme is real.
    """

    theme_id: str
    name_kr: str
    definition: str
    always_on: bool = False
    keywords: dict = field(default_factory=dict)
    related: list = field(default_factory=list)


@dataclass
class Catalog:
    version: int
    updated: str
    themes: dict  # theme_id -> Theme

    def primary_candidates(self) -> list[Theme]:
        """Themes to offer the tagging model as a symbol's core business.

        The backbone is excluded: macro applies to every symbol, so putting it
        on the menu only invites the model to pick it for all of them. Rules
        that match on names (index ETFs → macro) look at every theme instead.
        """
        return [t for t in self.themes.values() if not t.always_on]

    def get(self, theme_id: str) -> Optional[Theme]:
        return self.themes.get(theme_id)

    def validate(self) -> None:
        """Raise ValueError on referential / range violations."""
        for t in self.themes.values():
            for edge in t.related:
                if edge.get("theme") not in self.themes:
                    raise ValueError(
                        f"{t.theme_id} -> unknown related theme "
                        f"'{edge.get('theme')}'"
                    )
                w = edge.get("weight")
                if w is None or not (0.0 <= float(w) <= 1.0):
                    raise ValueError(
                        f"{t.theme_id} -> {edge.get('theme')} weight {w} "
                        "out of [0,1]"
                    )


def load_catalog(path: Optional[Path] = None) -> Catalog:
    """Load + validate the theme catalog. Defaults to bundled themes.json."""
    p = Path(path) if path else _CATALOG_PATH
    raw = json.loads(p.read_text(encoding="utf-8"))
    themes: dict[str, Theme] = {}
    for entry in raw["themes"]:
        t = Theme(
            theme_id=entry["theme_id"],
            name_kr=entry["name_kr"],
            definition=entry.get("definition", ""),
            always_on=bool(entry.get("always_on", False)),
            keywords=entry.get("keywords", {}),
            related=entry.get("related", []),
        )
        if t.theme_id in themes:
            raise ValueError(f"duplicate theme_id '{t.theme_id}'")
        themes[t.theme_id] = t
    cat = Catalog(version=raw.get("version", 0),
                  updated=raw.get("updated", ""), themes=themes)
    cat.validate()
    return cat


# ── sources ──────────────────────────────────────────────────────────────────
# group -> lead/lag lane. A research preprint leads the market, a Reddit thread
# moves with it, a news article trails it.
LEAD_LAG = {"research": "leading", "social": "coincident", "news": "lagging"}


@dataclass
class Feed:
    """One entry from sources.json.

    Attributes:
        source_id: stable id, e.g. "thelec_battery".
        kind:      'rss' | 'arxiv' | 'x'.
        group:     'research' | 'social' | 'news' — decides lead_lag.
        url:       feed url (rss).
        query:     search expression (arxiv).
        broad:     whole-site feed. Its off-topic articles are dropped by a
                   keyword gate before they cost an enrichment slot.
    """

    source_id: str
    kind: str
    group: str
    url: Optional[str] = None
    query: Optional[str] = None
    broad: bool = False

    @property
    def lead_lag(self) -> str:
        return LEAD_LAG.get(self.group, "lagging")


def load_sources(path: Optional[Path] = None) -> list[Feed]:
    """Load sources.json. Raises on a duplicate id or an unusable entry —
    a silently dropped feed is a field that quietly stops being covered."""
    p = Path(path) if path else _SOURCES_PATH
    raw = json.loads(p.read_text(encoding="utf-8"))
    feeds: list[Feed] = []
    seen: set[str] = set()
    for e in raw["sources"]:
        f = Feed(source_id=e["id"], kind=e["kind"], group=e["group"],
                 url=e.get("url"), query=e.get("query"),
                 broad=bool(e.get("broad", False)))
        if f.source_id in seen:
            raise ValueError(f"duplicate source id '{f.source_id}'")
        if f.kind == "rss" and not f.url:
            raise ValueError(f"rss source '{f.source_id}' has no url")
        if f.kind == "arxiv" and not f.query:
            raise ValueError(f"arxiv source '{f.source_id}' has no query")
        seen.add(f.source_id)
        feeds.append(f)
    return feeds


def all_keywords(catalog: Catalog) -> list[str]:
    """Every theme keyword, lowercased — the gate a broad feed's articles pass
    before they are stored at all."""
    out: set[str] = set()
    for t in catalog.themes.values():
        for lst in (t.keywords or {}).values():
            out.update(k.lower() for k in lst if k)
    return sorted(out)
