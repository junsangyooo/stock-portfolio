"""L4 step 3 — per-symbol qualitative (news/trend/issue) evaluation.

Triggered on-demand by the UI 📰 button (POST /api/analysis/news/{m}/{c}/run).
Reads the symbol's theme mapping (market.db symbol_themes) + each theme's rolling state
(refreshed by the collection cycle: collect→enrich→daily) and applies
src/agents/news/news_rules.md via claude -p to produce a qualitative verdict.

The rulebook drives a 4-lens read (per news_rules.md):
  §B priced-in → §C direction/magnitude → §D timing → §E second-order,
then §F attributes the theme signal to THIS symbol (primary / related+weight /
macro background).

Output uses the SAME 5-level label vocabulary as the chart / fundamental agents
so the 🧠 Judge can fuse all three. Written to data/analysis/news/{symbol}/
{date}.json, served by GET /api/analysis/news/{market}/{code}.

⚠️ No price/volume input by design — "priced-in" is inferred from the
information-diffusion stage (lead_lag / novelty / watchlist), NOT observed price
(rulebook §I). Cross-domain fusion is the Judge's job.

Pipeline:
  assemble_inputs — symbol_themes + per-theme state (primary full / related
                    brief + edge_relation from the primary theme's themes.json
                    edges / macro brief). Read-only — does NOT collect.
  _judge          — applies news_rules.md via claude -p → rich verdict
                    (priced_in / time_horizon / second_order / drivers / risks /
                    event_tags / evidence / watch + layperson summary_ko).
  evaluate_symbol — assemble → judge → persist.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from ...core.kst import KST as _KST
from ..llm_backend import ClaudeCliBackend, SLOW_MODEL, _parse_json
from ..verdict import clamp_confidence, clamp_score, label_from_score, persist
from .catalog import Catalog, load_catalog
from .read import theme_state

_RECENT_PRIMARY = 8   # recent developments injected for the primary theme
_RECENT_RELATED = 3   # ... for each related/macro theme (kept brief)

# 5-level label vocabulary, identical to the chart / fundamental agents.
_PRICED_IN = ("unpriced", "partially_priced", "mostly_priced")
_HORIZONS = ("immediate", "near", "mid", "long")

_RULES = (Path(__file__).parent / "news_rules.md").read_text(encoding="utf-8")


def get_symbol_themes(symbol_id: str) -> list[dict]:
    """Theme mapping for one symbol from market.db symbol_themes (primary first)."""
    from ...market.store import theme_store
    return theme_store.get(symbol_id)


# ── input assembly ────────────────────────────────────────────────────────

def _trim_dev(d: dict) -> dict:
    """Keep only the fields the rulebook references (novelty may be absent on
    pre-fix state files — the rulebook §B falls back to lead_lag/watchlist)."""
    out = {"date": d.get("date"), "point": d.get("point"),
           "lead_lag": d.get("lead_lag"), "weight": d.get("weight")}
    if "novelty" in d:
        out["novelty"] = d.get("novelty")
    return out


def _theme_block(state: dict, relation: str, weight: float,
                 edge_relation: Optional[str], name_kr: str, full: bool) -> dict:
    """One theme's state shaped for the prompt. primary=full, others=brief.

    `relation`      = symbol_themes relation (primary | related | macro) — §F.
    `edge_relation` = themes.json edge from the PRIMARY theme (upstream/
                      downstream/...) — §E read-through path (related only).
    """
    recent = state.get("recent_developments", []) or []
    n = _RECENT_PRIMARY if full else _RECENT_RELATED
    blk = {
        "theme_id": state.get("theme_id"),
        "name_kr": name_kr,
        "relation": relation,
        "weight": round(float(weight), 2),
        "horizon_summary": state.get("horizon_summary") or "",
        "sentiment": state.get("sentiment", {}) or {},
        "recent_developments": [_trim_dev(d) for d in recent[:n]],
    }
    if edge_relation:
        blk["edge_relation"] = edge_relation
    if full:
        blk["watchlist"] = (state.get("watchlist") or [])[:6]
        blk["last_shift"] = state.get("last_shift", "")
    return blk


def assemble_inputs(symbol_id: str, *, catalog: Optional[Catalog] = None) -> dict:
    """Gather the symbol's theme states for evaluation (read-only).

    primary theme  → full state (horizon + sentiment + recent + watchlist).
    related themes → brief state + `edge_relation` (its position in the primary
                     theme's themes.json value chain → §E read-through path).
    macro          → brief state, always injected as the backbone (§F3).
    """
    catalog = catalog or load_catalog()
    themes = get_symbol_themes(symbol_id)

    # edge_relation map: the primary theme's themes.json related edges
    # ({related_theme_id: relation}) — used to annotate related themes (§E1).
    primary_id = next((t["theme_id"] for t in themes
                       if t["relation"] == "primary"), None)
    edge_map: dict[str, str] = {}
    if primary_id:
        pt = catalog.get(primary_id)
        if pt:
            for e in pt.related:
                if e.get("theme") and e.get("relation"):
                    edge_map[e["theme"]] = e["relation"]

    blocks: list[dict] = []
    used: list[dict] = []
    have_macro = False
    for t in themes:
        st = theme_state(t["theme_id"])
        ct = catalog.get(t["theme_id"])
        name_kr = ct.name_kr if ct else t["theme_id"]
        full = t["relation"] == "primary"
        edge = edge_map.get(t["theme_id"]) if t["relation"] == "related" else None
        blocks.append(_theme_block(st, t["relation"], float(t["weight"]),
                                   edge, name_kr, full))
        used.append(t)
        if t["theme_id"] == "macro":
            have_macro = True

    # Always inject macro as background backbone (§F3) if not already mapped.
    macro_ct = catalog.get("macro")
    if not have_macro and macro_ct:
        blocks.append(_theme_block(theme_state("macro"), "macro", 0.2, None,
                                   macro_ct.name_kr, full=False))
        used.append({"theme_id": "macro", "relation": "macro", "weight": 0.2})

    n_dev = sum(len(b.get("recent_developments", [])) for b in blocks)
    has_content = any(
        b.get("recent_developments") or b.get("horizon_summary")
        or b.get("watchlist") for b in blocks
    )
    return {
        "symbol_id": symbol_id,
        "primary_theme": primary_id,
        "themes": blocks,
        "themes_used": used,
        "data_available": {
            "themes_mapped": bool(themes),
            "has_content": has_content,
            "n_developments": n_dev,
        },
    }


# ── judgment (claude -p applies the rulebook) ───────────────────────────────

# System directive: 4-lens contract + layperson-first output + the rulebook.

# The per-domain judgment (_judge / evaluate_symbol) lived here until
# 2026-09-01. It is gone: judging a domain in isolation hardens a
# one-dimensional conclusion, and the merge that followed could no longer undo
# it. src/agents/analyze/ now sees all three domains in one call, with this
# module's assembly as its input and news_rules.md injected as that domain's
# dictionary.
