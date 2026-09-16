"""What the daily model spend is bought for.

`to_analyze` decides which symbols get a call, the jobs table decides which
job gets which list, and `--hot` decides which of them keep the full
reasoning budget. Each is a line or two, none had a test, and getting any of
them backwards is silent: the bill moves, nothing fails.
"""
from __future__ import annotations

import sys

import pytest

from src.portfolio import jobs as pjobs
from src.quant import jobs as qjobs
from src.research import analyze, verdict_job


@pytest.fixture
def seen_due(monkeypatch):
    """Captures what the selector was asked, and answers with the first one."""
    seen: dict = {}

    def _due(symbols, *, hot=(), **kw):
        seen["symbols"], seen["hot"] = list(symbols), list(hot)
        return [{"symbol_id": s, "reason": "age", "detail": "9거래일",
                 "age_days": 9} for s in list(symbols)[:1]]

    monkeypatch.setattr(analyze, "due_detail", _due)
    return seen


@pytest.fixture
def quant_book(monkeypatch):
    monkeypatch.setattr(qjobs, "account_ids", lambda: ["robo", "robo_paper"])
    monkeypatch.setattr(qjobs, "active_markets", lambda a: {"KR", "US"})
    monkeypatch.setattr(qjobs, "analysis_symbols",
                        lambda a: ["KR:HELD", "KR:PLANNED"] if a == "robo"
                        else ["KR:PLANNED"])
    monkeypatch.setattr(qjobs, "plan_symbols",
                        lambda a: ["KR:PLANNED"] if a == "robo" else [])
    monkeypatch.setattr(qjobs, "tracked_symbols",
                        lambda a: ["KR:HELD", "KR:PLANNED", "KR:WATCHED"])


@pytest.fixture
def human_book(monkeypatch):
    monkeypatch.setattr(pjobs, "all_symbol_ids", lambda: ["KR:HELD"])
    monkeypatch.setattr(pjobs, "all_active_symbol_ids", lambda: ["KR:PLANNED"])


# ── who gets a model call ───────────────────────────────────────────────────

def test_the_quant_analysis_set_is_held_and_planned_and_hot_is_the_plans(
        quant_book, seen_due):
    assert qjobs.to_analyze() == ["KR:HELD"]
    assert seen_due["symbols"] == ["KR:HELD", "KR:PLANNED"]   # no KR:WATCHED
    assert seen_due["hot"] == ["KR:PLANNED"]


def test_the_human_analysis_set_is_held_and_planned(human_book, seen_due):
    assert pjobs.to_analyze() == ["KR:HELD"]
    assert seen_due["symbols"] == ["KR:HELD", "KR:PLANNED"]
    assert seen_due["hot"] == ["KR:PLANNED"]


def test_force_skips_the_selector_entirely(quant_book, human_book, seen_due):
    assert qjobs.to_analyze(force=True) == ["KR:HELD", "KR:PLANNED"]
    assert pjobs.to_analyze(force=True) == ["KR:HELD", "KR:PLANNED"]
    assert seen_due == {}


# ── which job gets which list ───────────────────────────────────────────────

def _capture(monkeypatch, mod):
    calls: list = []
    monkeypatch.setattr(
        mod, "_run_module",
        lambda module, symbols, extra: calls.append(
            (module, list(symbols), list(extra))) or 0)
    return calls


def test_market_data_still_covers_everything_tracked(monkeypatch, quant_book,
                                                     seen_due):
    """Only the model call is narrowed. Swapping these lists would quietly
    stop collecting bars for symbols the book still watches."""
    calls = _capture(monkeypatch, qjobs)
    monkeypatch.setattr(sys, "argv", ["jobs", "all"])
    qjobs.main()
    by = {m: (s, e) for m, s, e in calls}
    assert by["src.market.pipeline.fundamental_job"][0] == [
        "KR:HELD", "KR:PLANNED", "KR:WATCHED"]
    assert by["src.market.pipeline.etf_job"][0] == by[
        "src.market.pipeline.fundamental_job"][0]
    assert by["src.research.verdict_job"][0] == ["KR:HELD"]


def test_the_analysis_job_is_told_the_budget_and_who_keeps_it(
        monkeypatch, quant_book, seen_due):
    calls = _capture(monkeypatch, qjobs)
    monkeypatch.setattr(sys, "argv", ["jobs", "analysis"])
    qjobs.main()
    extra = calls[0][2]
    assert extra[extra.index("--effort") + 1] == qjobs.LOW_EFFORT
    assert extra[extra.index("--hot") + 1] == "KR:PLANNED"


def test_the_human_book_passes_the_same_two_flags(monkeypatch, human_book,
                                                  seen_due):
    calls = _capture(monkeypatch, pjobs)
    monkeypatch.setattr(sys, "argv", ["jobs", "analysis"])
    pjobs.main()
    extra = calls[0][2]
    assert extra[extra.index("--effort") + 1] == pjobs.LOW_EFFORT
    assert extra[extra.index("--hot") + 1] == "KR:PLANNED"


# ── who keeps the full budget ───────────────────────────────────────────────

def test_only_symbols_nobody_is_waiting_on_get_the_cheap_budget(monkeypatch):
    """Invert this ternary and every symbol carrying money drops to `low`
    while the idle ones burn the full budget — and nothing fails."""
    seen: dict = {}

    def _run(symbol_id, date, *, force, effort=None, root=None):
        seen[symbol_id] = {"effort": effort, "root": root}
        return {"symbol": symbol_id, "status": "ok", "errors": []}

    monkeypatch.setattr(verdict_job, "run_symbol", _run)
    monkeypatch.setattr(analyze, "on", lambda sid, date: None)
    monkeypatch.setattr(analyze, "batch_root", lambda: "sess-1")
    monkeypatch.setattr(sys, "argv",
                        ["verdict_job", "--symbols", "KR:AAA,KR:BBB",
                         "--effort", "low", "--hot", "kr:bbb", "--workers", "1"])
    verdict_job.main()
    assert seen["KR:AAA"]["effort"] == "low"
    assert seen["KR:BBB"]["effort"] is None        # money is waiting on it
    assert seen["KR:AAA"]["root"] == "sess-1"      # two to run → a root pays


def test_a_single_symbol_batch_does_not_open_a_root(monkeypatch):
    """A root costs about what one full prompt costs; below two symbols it is
    a loss."""
    roots: list = []

    monkeypatch.setattr(verdict_job, "run_symbol",
                        lambda s, d, **kw: roots.append(kw.get("root"))
                        or {"symbol": s, "status": "ok", "errors": []})
    monkeypatch.setattr(analyze, "on", lambda sid, date: None)
    monkeypatch.setattr(analyze, "batch_root",
                        lambda: pytest.fail("root opened for one symbol"))
    monkeypatch.setattr(sys, "argv",
                        ["verdict_job", "--symbols", "KR:AAA", "--workers", "1"])
    verdict_job.main()
    assert roots == [None]
