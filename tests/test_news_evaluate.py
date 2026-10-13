"""Tests for the news domain's deterministic assembly.

The per-domain judgment that used to live beside this was removed on
2026-09-01 — the three domains meet inside one call now
(tests/test_analyze.py). What remains here is the assembly that call reads."""
from __future__ import annotations

import pytest

from src.research.news import inputs as ev
from src.research.news.inputs import get_symbol_themes, assemble_inputs
from src.research.news.catalog import load_catalog


@pytest.fixture
def mapped():
    """One symbol mapped into a primary + two related themes.

    Writes to the throwaway market.db the autouse conftest fixture points at.
    """
    from src.market.store import theme_store
    theme_store.save("KR:247540", [
        {"theme_id": "battery", "relation": "primary", "weight": 1.0},
        {"theme_id": "ev", "relation": "related", "weight": 0.8},
        {"theme_id": "materials_raw", "relation": "related", "weight": 0.7},
    ])


def _fake_state(theme_id):
    """Deterministic non-empty state so assemble_inputs has content."""
    return {
        "theme_id": theme_id,
        "horizon_summary": f"{theme_id} 장기추세",
        "sentiment": {"score": 0.4, "trend": "improving"},
        "recent_developments": [
            {"date": "2026-05-24", "point": "전개1", "lead_lag": "leading",
             "weight": 0.8, "novelty": True, "url": "u1"},
        ],
        "watchlist": ["이슈A"],
        "last_shift": "",
    }


# ── theme lookup ────────────────────────────────────────────────────────────

def test_get_symbol_themes_primary_first(mapped):
    rows = get_symbol_themes("KR:247540")
    assert rows[0]["theme_id"] == "battery" and rows[0]["relation"] == "primary"
    assert {r["theme_id"] for r in rows} == {"battery", "ev", "materials_raw"}


def test_get_symbol_themes_empty(mapped):
    assert get_symbol_themes("KR:000000") == []


# ── input assembly ──────────────────────────────────────────────────────────

def test_assemble_inputs_shape_and_edges(mapped, monkeypatch):
    monkeypatch.setattr(ev, "theme_state", _fake_state)
    cat = load_catalog()
    inp = assemble_inputs("KR:247540", catalog=cat)

    assert inp["primary_theme"] == "battery"
    assert inp["data_available"]["themes_mapped"] is True
    assert inp["data_available"]["has_content"] is True

    blocks = {b["theme_id"]: b for b in inp["themes"]}
    # primary carries watchlist; related does not
    assert "watchlist" in blocks["battery"] and blocks["battery"]["relation"] == "primary"
    assert "watchlist" not in blocks["ev"]
    # edge_relation pulled from the primary theme's themes.json edges
    assert blocks["ev"]["edge_relation"] == "downstream"
    assert blocks["materials_raw"]["edge_relation"] == "upstream"
    # macro backbone auto-injected (not in symbol_themes)
    assert "macro" in blocks and blocks["macro"]["relation"] == "macro"
