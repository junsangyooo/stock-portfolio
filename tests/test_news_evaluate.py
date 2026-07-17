"""Tests for L4 evaluate: theme lookup (sqlite), input assembly (with
edge_relation from the catalog), and the rulebook judge (stubbed claude)."""
from __future__ import annotations

import sqlite3

import pytest

from src.agents.news import evaluate as ev
from src.agents.news.evaluate import (
    get_symbol_themes, assemble_inputs, _judge,
    clamp_score, label_from_score,
)
from src.agents.news.catalog import load_catalog


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.execute(
        "CREATE TABLE holding_themes (symbol_id TEXT, theme_id TEXT, "
        "relation TEXT, weight REAL, source TEXT, updated_at TEXT, "
        "PRIMARY KEY (symbol_id, theme_id))"
    )
    c.executemany(
        "INSERT INTO holding_themes VALUES (?,?,?,?,?,?)",
        [
            ("KR:247540", "battery", "primary", 1.0, "auto", "t"),
            ("KR:247540", "ev", "related", 0.8, "auto", "t"),
            ("KR:247540", "materials_raw", "related", 0.7, "auto", "t"),
        ],
    )
    c.commit()
    return c


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

def test_get_symbol_themes_primary_first(conn):
    rows = get_symbol_themes("KR:247540", conn)
    assert rows[0]["theme_id"] == "battery" and rows[0]["relation"] == "primary"
    assert {r["theme_id"] for r in rows} == {"battery", "ev", "materials_raw"}


def test_get_symbol_themes_empty(conn):
    assert get_symbol_themes("KR:000000", conn) == []


# ── input assembly ──────────────────────────────────────────────────────────

def test_assemble_inputs_shape_and_edges(conn, monkeypatch):
    monkeypatch.setattr(ev, "load_state", _fake_state)
    cat = load_catalog()
    inp = assemble_inputs("KR:247540", catalog=cat, conn=conn)

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
    # macro backbone auto-injected (not in holding_themes)
    assert "macro" in blocks and blocks["macro"]["relation"] == "macro"


# ── judge (stubbed claude) ───────────────────────────────────────────────────

class _StubBackend:
    def __init__(self, payload):
        self.payload = payload
        self.called = False

    def call(self, system, user, timeout_s=60, model=None):
        self.called = True
        return self.payload


class _ExplodingBackend:
    def call(self, *a, **k):
        raise AssertionError("backend must not be called on empty input")


def _ok_inputs():
    return {
        "symbol_id": "KR:247540", "primary_theme": "battery",
        "themes": [{"theme_id": "battery", "relation": "primary", "weight": 1.0,
                    "horizon_summary": "h", "sentiment": {}, "recent_developments": [],
                    "watchlist": []}],
        "themes_used": [{"theme_id": "battery", "relation": "primary", "weight": 1.0}],
        "data_available": {"themes_mapped": True, "has_content": True, "n_developments": 1},
    }


def test_judge_clamps_score_and_extracts_rich_fields():
    stub = _StubBackend(
        '{"score": 1.7, "summary_ko": "쉬운결론", "priced_in": "unpriced", '
        '"time_horizon": "long", "rationale": "강세", "drivers": ["수요"], '
        '"risks": ["공급과잉"], "second_order": ["A → B"], "event_tags": ["기술돌파"], '
        '"evidence": [{"theme":"battery","point":"x","lead_lag":"leading"}], '
        '"watch": ["w1"], "confidence": 0.6, "methodology": ["B2","C2"]}'
    )
    res = _judge("KR:247540", "에코프로비엠", _ok_inputs(), backend=stub)
    assert res["status"] == "ok"
    assert res["score"] == 1.0 and res["label"] == "strong_buy"   # clamped 1.7
    assert res["priced_in"] == "unpriced" and res["time_horizon"] == "long"
    assert res["summary_ko"] == "쉬운결론"
    assert res["second_order"] == ["A → B"] and res["event_tags"] == ["기술돌파"]
    assert res["confidence"] == 0.6 and res["methodology"] == ["B2", "C2"]


def test_judge_negative_and_enum_coercion():
    stub = _StubBackend(
        '{"score": -0.3, "priced_in": "bogus", "time_horizon": "soon", '
        '"drivers": [], "risks": ["규제"]}'
    )
    res = _judge("KR:000000", "X", _ok_inputs(), backend=stub)
    assert res["score"] == -0.3 and res["label"] == "sell"
    assert res["priced_in"] is None and res["time_horizon"] is None  # invalid -> None
    assert res["evidence"] == [] and res["second_order"] == []       # missing -> default


def test_judge_no_mapping_short_circuits():
    inp = {"themes": [], "themes_used": [],
           "data_available": {"themes_mapped": False, "has_content": False}}
    res = _judge("KR:000000", "X", inp, backend=_ExplodingBackend())
    assert res["status"] == "insufficient_data" and res["label"] == "neutral"
    assert res["score"] == 0.0 and res["confidence"] == 0.0


def test_judge_no_content_surfaces_watchlist():
    inp = {
        "themes": [{"theme_id": "battery", "relation": "primary", "weight": 1.0,
                    "watchlist": ["예정 카탈리스트"], "recent_developments": [],
                    "horizon_summary": ""}],
        "themes_used": [],
        "data_available": {"themes_mapped": True, "has_content": False},
    }
    res = _judge("KR:247540", "X", inp, backend=_ExplodingBackend())
    assert res["status"] == "insufficient_data" and res["label"] == "neutral"
    assert res["watch"] == ["예정 카탈리스트"]   # future catalyst != no signal (§H14)


# ── label / clamp utils ──────────────────────────────────────────────────────

def test_label_thresholds():
    assert label_from_score(-0.9) == "strong_sell"
    assert label_from_score(-0.3) == "sell"
    assert label_from_score(0.0) == "neutral"
    assert label_from_score(0.3) == "buy"
    assert label_from_score(0.9) == "strong_buy"
    assert clamp_score(2.0) == 1.0 and clamp_score(-2.0) == -1.0
