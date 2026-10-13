"""What the daily model spend is bought for.

`to_analyze` decides which symbols get a call, the jobs table decides which
job gets which list, and `--effort` decides the reasoning budget. Each is a
line or two, and getting any of them backwards is silent: the bill moves,
nothing fails.
"""
from __future__ import annotations

import sys

import pytest

from src.portfolio import supply_job as pjobs
from src.research import analyze
from src.research.analyze import batch_job


@pytest.fixture
def seen_due(monkeypatch):
    """Captures what the selector was asked, and answers with the first one."""
    seen: dict = {}

    def _due(symbols, **kw):
        seen["symbols"] = list(symbols)
        return [{"symbol_id": s, "reason": "age", "detail": "9거래일",
                 "age_days": 9} for s in list(symbols)[:1]]

    monkeypatch.setattr(analyze, "due_detail", _due)
    return seen


@pytest.fixture
def human_book(monkeypatch):
    monkeypatch.setattr(pjobs, "all_symbol_ids", lambda: ["KR:HELD"])


# ── who gets a model call ───────────────────────────────────────────────────

def test_the_human_analysis_set_is_held_and_changed(human_book, seen_due):
    assert pjobs.to_analyze() == ["KR:HELD"]
    assert seen_due["symbols"] == ["KR:HELD"]


def test_force_skips_the_selector_entirely(human_book, seen_due):
    assert pjobs.to_analyze(force=True) == ["KR:HELD"]
    assert seen_due == {}


# ── which job gets which list ───────────────────────────────────────────────

def _capture(monkeypatch, mod):
    calls: list = []
    monkeypatch.setattr(
        mod, "_run_module",
        lambda module, symbols, extra: calls.append(
            (module, list(symbols), list(extra))) or 0)
    return calls


def test_market_data_still_covers_every_held_symbol(monkeypatch, seen_due):
    """Only the model call is narrowed. Swapping these lists would quietly
    stop collecting bars for symbols the book still holds."""
    monkeypatch.setattr(pjobs, "all_symbol_ids", lambda: ["KR:CHANGED", "KR:QUIET"])
    calls = _capture(monkeypatch, pjobs)
    monkeypatch.setattr(sys, "argv", ["jobs", "all"])
    pjobs.main()
    by = {m: s for m, s, e in calls}
    assert by["src.market.pipeline.fundamental_job"] == ["KR:CHANGED", "KR:QUIET"]
    assert by["src.market.pipeline.etf_job"] == by["src.market.pipeline.fundamental_job"]
    assert by["src.research.analyze.batch_job"] == ["KR:CHANGED"]   # what the selector kept


def test_the_human_book_runs_at_its_budget_with_nothing_hot(monkeypatch, human_book,
                                                           seen_due):
    calls = _capture(monkeypatch, pjobs)
    monkeypatch.setattr(sys, "argv", ["jobs", "analysis"])
    pjobs.main()
    extra = calls[0][2]
    assert extra[extra.index("--effort") + 1] == pjobs.LOW_EFFORT


# ── who keeps the full budget ───────────────────────────────────────────────

def test_the_batch_passes_its_budget_to_every_symbol(monkeypatch):
    seen: dict = {}

    def _run(symbol_id, date, *, force, effort=None, root=None):
        seen[symbol_id] = {"effort": effort, "root": root}
        return {"symbol": symbol_id, "status": "ok", "errors": []}

    monkeypatch.setattr(batch_job, "run_symbol", _run)
    monkeypatch.setattr(analyze, "on", lambda sid, date: None)
    roots: list = []
    monkeypatch.setattr(analyze, "batch_root",
                        lambda effort=None: roots.append(effort) or "sess-1")
    monkeypatch.setattr(sys, "argv",
                        ["batch_job", "--symbols", "KR:AAA,KR:BBB",
                         "--effort", "low", "--workers", "1"])
    batch_job.main()
    assert seen["KR:AAA"]["effort"] == seen["KR:BBB"]["effort"] == "low"
    assert seen["KR:AAA"]["root"] == "sess-1"      # two to run → a root pays
    # the root is made at the forks' budget, or the forks miss its cache
    assert roots == ["low"]


def test_a_single_symbol_batch_does_not_open_a_root(monkeypatch):
    """A root costs about what one full prompt costs; below two symbols it is
    a loss."""
    roots: list = []

    monkeypatch.setattr(batch_job, "run_symbol",
                        lambda s, d, **kw: roots.append(kw.get("root"))
                        or {"symbol": s, "status": "ok", "errors": []})
    monkeypatch.setattr(analyze, "on", lambda sid, date: None)
    monkeypatch.setattr(analyze, "batch_root",
                        lambda **kw: pytest.fail("root opened for one symbol"))
    monkeypatch.setattr(sys, "argv",
                        ["batch_job", "--symbols", "KR:AAA", "--workers", "1"])
    batch_job.main()
    assert roots == [None]
