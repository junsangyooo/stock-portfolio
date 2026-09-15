"""Which stored analyses come up for a redo.

The rule this replaces was "all of them, every day", so the only thing these
tests really have to pin is that a quiet symbol stays quiet and every kind of
change still gets through.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from src.core import paths
from src.core.kst import KST
from src.research.analyze import due
from src.research.verdict import KIND

NOW = datetime(2026, 9, 15, 9, 0, tzinfo=KST)        # Tuesday
YESTERDAY = "2026-09-14"
TWO_WEEKS = "2026-09-02"                             # 9 weekdays back


def _store(sid: str, date: str, *, current=10_000.0, invalidation=9_000.0,
           target=12_000.0, low=9_400.0, high=9_600.0) -> None:
    d = paths.ANALYSIS_DIR / KIND / sid.replace(":", "_")
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{date.replace('-', '')}.json").write_text(json.dumps({
        "symbol_id": sid, "date": date, "status": "ok",
        "levels": {"current": current,
                   "invalidation": {"price": invalidation},
                   "targets": [{"price": target}],
                   "entry_zone": {"low": low, "high": high}},
    }, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def sources(monkeypatch):
    """The three stored-data reads, stubbed. Each test says what changed."""
    data: dict = {"closes": {}, "filed": {}, "mentions": {}}
    monkeypatch.setattr(due, "_closes", lambda ids: data["closes"])
    monkeypatch.setattr(due, "_filings", lambda ids: data["filed"])
    monkeypatch.setattr(due, "_mentions", lambda ids: data["mentions"])
    return data


def _due(*sids, hot=(), limit=due.DAILY_LIMIT):
    return due.due_among(sids, hot=hot, limit=limit, now=NOW)


def _reason(sid, **kw):
    rows = due.due_detail([sid], now=NOW, **kw)
    return rows[0]["reason"] if rows else None


# ── the triggers ────────────────────────────────────────────────────────────

def test_a_symbol_never_analysed_is_always_due(sources):
    assert _due("KR:AAA") == ["KR:AAA"]
    assert _reason("KR:AAA") == "missing"


def test_a_result_with_no_judgment_is_always_due(sources):
    """`facts_only` is a failed call that kept the numbers: no verdict, no
    coordinates, so no other trigger can fire on it. It must not sit there
    until it ages out — 49 of 81 symbols were in that state after the weekly
    limit ran out."""
    _store("KR:AAA", YESTERDAY)
    path = (paths.ANALYSIS_DIR / KIND / "KR_AAA" /
            f"{YESTERDAY.replace('-', '')}.json")
    body = json.loads(path.read_text(encoding="utf-8"))
    body["status"] = "facts_only"
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    sources["closes"] = {"KR:AAA": [10_000.0]}
    assert _reason("KR:AAA") == "facts_only"


def test_a_quiet_symbol_is_left_alone(sources):
    _store("KR:AAA", YESTERDAY)
    sources["closes"] = {"KR:AAA": [10_050.0]}       # +0.5%, no coordinate
    assert _due("KR:AAA") == []


def test_a_close_through_the_invalidation_is_due(sources):
    _store("KR:AAA", YESTERDAY)
    sources["closes"] = {"KR:AAA": [8_900.0]}
    assert _reason("KR:AAA") == "level"


def test_a_close_at_the_target_is_due(sources):
    _store("KR:AAA", YESTERDAY)
    sources["closes"] = {"KR:AAA": [12_100.0]}
    assert _reason("KR:AAA") == "level"


def test_a_close_inside_the_entry_zone_is_due(sources):
    _store("KR:AAA", YESTERDAY)
    sources["closes"] = {"KR:AAA": [9_500.0]}
    assert _reason("KR:AAA") == "level"


def test_a_filing_after_the_analysis_is_due_and_one_before_is_not(sources):
    _store("KR:AAA", YESTERDAY)
    sources["closes"] = {"KR:AAA": [10_000.0]}
    sources["filed"] = {"KR:AAA": "2026-09-01"}
    assert _due("KR:AAA") == []
    sources["filed"] = {"KR:AAA": "2026-09-15"}
    assert _reason("KR:AAA") == "filing"


def test_an_important_article_after_the_analysis_is_due(sources):
    _store("KR:AAA", YESTERDAY)
    sources["closes"] = {"KR:AAA": [10_000.0]}
    sources["mentions"] = {"KR:AAA": "2026-09-15"}
    assert _reason("KR:AAA") == "news"


def test_a_move_past_the_threshold_is_due_without_touching_a_coordinate(sources):
    _store("KR:AAA", YESTERDAY, invalidation=1.0, target=99_999.0,
           low=1.0, high=2.0)
    sources["closes"] = {"KR:AAA": [10_600.0]}       # +6%
    assert _reason("KR:AAA") == "move"


def test_age_is_the_backstop_when_nothing_else_fired(sources):
    _store("KR:AAA", TWO_WEEKS)
    sources["closes"] = {"KR:AAA": [10_000.0]}
    assert _reason("KR:AAA") == "age"


# ── how much of it runs ─────────────────────────────────────────────────────

def test_a_symbol_carrying_a_plan_gets_the_shorter_age_limit(sources):
    """Three weekdays old: too old for money waiting on it, fine otherwise."""
    _store("KR:AAA", "2026-09-10")                   # Thu → 3 weekdays back
    _store("KR:BBB", "2026-09-10")
    sources["closes"] = {"KR:AAA": [10_000.0], "KR:BBB": [10_000.0]}
    assert _due("KR:AAA", "KR:BBB", hot=["KR:AAA"]) == ["KR:AAA"]


def test_the_limit_keeps_the_urgent_ones(sources):
    _store("KR:OLD", TWO_WEEKS)                      # age
    _store("KR:BROKE", YESTERDAY)                    # level
    sources["closes"] = {"KR:OLD": [10_000.0], "KR:BROKE": [8_000.0]}
    assert _due("KR:OLD", "KR:BROKE", "KR:NEW", limit=2) == ["KR:NEW", "KR:BROKE"]


def test_nothing_asked_is_nothing_answered(sources):
    assert due.due_among([], now=NOW) == []


def test_a_holiday_can_only_make_a_symbol_early_never_late():
    """Weekdays stand in for trading days: the count is never short."""
    d = datetime(2026, 9, 11, tzinfo=KST).date()     # Fri
    assert due._weekdays_since(d, datetime(2026, 9, 14, tzinfo=KST).date()) == 1
    assert due._weekdays_since(d, datetime(2026, 9, 15, tzinfo=KST).date()) == 2
