"""Nothing stale may pass as current.

Four separate paths let a frozen feed look like a live one, and the freeze
they produced ran twelve days before anyone noticed — every job reported
`ok` throughout, because each layer only ever asked "is there data" and
never "how old is it".

  [0] the auto book's symbols were never gap-filled at all: phase [0] called
      refresh_all, which answers for portfolio.db, so quant's bars stopped
      the day someone last backfilled by hand and valuation — derived from
      those bars — stopped with them
  [1] quote_store.get() with no ceiling handed out prices up to 108 days old
      to the account valuation and to the cockpit
  [2] the chart input checked bar COUNT, never bar DATE, so an analysis
      computed on stale bars went out stamped with today and status ok
  [3] an account with no `cash` row read as an account with no money, so a
      guard rejected buys for "cash 0" — a fact nobody had established

Each test below fails if one of those comes back.
"""
import json
import time
from datetime import datetime, timedelta

import pytest

from src.core.kst import KST
from src.market.store import quote_store
from src.market.source.base import Quote


# ── [0] the auto book gap-fills its own symbols ──────────────────────────

def test_cycle_phase0_fills_the_auto_books_own_symbols(monkeypatch):
    """refresh_all covers portfolio.db; the auto accounts must hand over
    theirs too, or every symbol only quant tracks stops getting bars."""
    from src.quant import cycle_job

    filled: list[list[str]] = []
    warmed: list[list[str]] = []

    monkeypatch.setattr("src.api.market.refresh_all",
                        lambda: {"ok": True, "symbols": 2})
    monkeypatch.setattr("src.api.market.warm",
                        lambda sids, max_age_s: (warmed.append(sorted(sids))
                                                 or {s: object() for s in sids}))
    monkeypatch.setattr("src.market.pipeline.gap_filler.fill_many",
                        lambda sids: (filled.append(sorted(sids))
                                      or {s: 1 for s in sids}))
    monkeypatch.setattr(cycle_job.store, "tracked_symbols",
                        lambda a: {"robo": ["KR:000500", "US:MU"],
                                   "robo_paper": ["US:MU", "US:DHT"]}[a])

    out = cycle_job._phase0_data(["robo", "robo_paper"])

    assert filled == [["KR:000500", "US:DHT", "US:MU"]], \
        "phase [0] must gap-fill the union of the auto accounts' symbols"
    assert warmed == [["KR:000500", "US:DHT", "US:MU"]], \
        "the same symbols need quotes, not just bars"
    assert out["auto_symbols"] == 3 and out["auto_bars"] == 3


def test_cycle_phase0_survives_a_dead_shared_refresh(monkeypatch):
    """A failure on the human side must not take the auto side down with it
    — they are separate books and separate reasons to fail."""
    from src.quant import cycle_job

    def _boom():
        raise RuntimeError("portfolio.db is locked")

    monkeypatch.setattr("src.api.market.refresh_all", _boom)
    monkeypatch.setattr("src.api.market.warm", lambda sids, max_age_s: {})
    monkeypatch.setattr("src.market.pipeline.gap_filler.fill_many",
                        lambda sids: {s: 1 for s in sids})
    monkeypatch.setattr(cycle_job.store, "tracked_symbols", lambda a: ["US:MU"])

    out = cycle_job._phase0_data(["robo"])
    assert out["ok"] is False and "locked" in out["error"]
    assert out["auto_bars"] == 1, "the auto book still gets its bars"


def test_an_account_with_its_markets_off_still_gets_its_data(monkeypatch):
    """A disabled market is not off: its exits are still guarded and watched
    on these prices, so the fetch must not be skipped."""
    from src.quant import cycle_job

    calls: list[list[str]] = []
    monkeypatch.setattr(cycle_job, "_phase0_data",
                        lambda ids: calls.append(list(ids)) or {"ok": True})
    monkeypatch.setattr(cycle_job, "run_account", lambda *a, **k: 0)
    monkeypatch.setattr(cycle_job.store, "account_ids", lambda: ["robo"])
    monkeypatch.setattr(cycle_job.store, "trade_mode", lambda a: "live")
    monkeypatch.setattr(cycle_job.store, "tracked_symbols", lambda a: [])

    cycle_job.run_cycle("evening")
    assert calls == [["robo"]], "a market-off account's data must still refresh"


def test_the_kill_switch_still_skips_the_fetch(monkeypatch):
    from src.quant import cycle_job

    calls = []
    monkeypatch.setattr(cycle_job, "_phase0_data",
                        lambda ids: calls.append(ids) or {"ok": True})
    monkeypatch.setattr(cycle_job, "run_account", lambda *a, **k: 0)
    monkeypatch.setattr(cycle_job.store, "account_ids", lambda: ["robo"])
    monkeypatch.setattr(cycle_job.store, "trade_mode", lambda a: "off")
    monkeypatch.setattr(cycle_job.store, "tracked_symbols", lambda a: [])

    cycle_job.run_cycle("evening")
    assert calls == [], "mode=off is the kill switch — nothing runs"


# ── [1] a quote old enough to be wrong is not a quote ────────────────────

@pytest.fixture
def _clean_quotes(tmp_path, monkeypatch):
    monkeypatch.setattr(quote_store, "_FILE", tmp_path / "last.json")
    monkeypatch.setattr(quote_store, "_cache", {})
    monkeypatch.setattr(quote_store, "_loaded", True)


def test_quote_older_than_the_ceiling_reads_as_no_quote(_clean_quotes):
    old = int(time.time()) - quote_store.STALE_S - 60
    quote_store.set_many([Quote("KR:005930", 70000.0, 69000.0, old)])
    assert quote_store.get("KR:005930") is not None, "no ceiling, no filter"
    assert quote_store.get("KR:005930",
                           max_age_s=quote_store.STALE_S) is None


def test_the_ceiling_clears_a_long_holiday(_clean_quotes):
    """설·추석 can close KRX for five straight sessions. A real price from
    before one must survive, or the account page empties for a week."""
    five_sessions_ago = int(time.time()) - 6 * 86400
    quote_store.set_many([Quote("KR:005930", 70000.0, 69000.0,
                                five_sessions_ago)])
    assert quote_store.get("KR:005930",
                           max_age_s=quote_store.STALE_S) is not None


@pytest.mark.parametrize("module,attr", [
    ("src.portfolio.service", None),
    ("src.quant.service", None),
    ("src.api.quant", None),
])
def test_every_valuation_path_passes_a_ceiling(module, attr):
    """Whoever turns a quote into money or into a judgment must bound its
    age. The poller may read the cache raw; these four may not."""
    import ast
    import importlib
    from pathlib import Path

    src = Path(importlib.import_module(module).__file__).read_text()
    tree = ast.parse(src)
    bare = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not (isinstance(f, ast.Attribute) and f.attr == "get"
                and isinstance(f.value, ast.Attribute)
                and f.value.attr == "quote_store"):
            if not (isinstance(f, ast.Attribute) and f.attr == "get"
                    and isinstance(f.value, ast.Name)
                    and f.value.id == "quote_store"):
                continue
        if not any(k.arg == "max_age_s" for k in node.keywords):
            bare.append(node.lineno)
    assert not bare, (f"{module}: quote_store.get without max_age_s at lines "
                      f"{bare} — a dead feed's last price would read as live")


# ── [2] bar count is not bar date ────────────────────────────────────────

def test_chart_input_flags_bars_that_stopped(monkeypatch):
    from src.research.analyze import inputs

    old = (datetime.now(KST) - timedelta(days=30)).strftime("%Y-%m-%d")
    monkeypatch.setattr("src.research.chart.chart_agent.collect_facts",
                        lambda sid, force=False: {
                            "asof": old,
                            "timeframes": {"1d": {"has_data": True},
                                           "1w": {"has_data": True},
                                           "1M": {"has_data": True}}})
    data, status, note = inputs._chart("KR:000500", force=False)
    assert data is not None, "the facts are still worth showing"
    assert status == "partial", "a 30-day-old bar is not an `ok` chart"
    assert old in note and "30일 전" in note


def test_chart_input_is_ok_on_current_bars(monkeypatch):
    from src.research.analyze import inputs

    today = datetime.now(KST).strftime("%Y-%m-%d")
    monkeypatch.setattr("src.research.chart.chart_agent.collect_facts",
                        lambda sid, force=False: {
                            "asof": today,
                            "timeframes": {"1d": {"has_data": True},
                                           "1w": {"has_data": True},
                                           "1M": {"has_data": True}}})
    _, status, note = inputs._chart("KR:005930", force=False)
    assert status == "ok" and note == ""


def test_chart_input_keeps_saying_when_a_timeframe_is_short(monkeypatch):
    from src.research.analyze import inputs

    today = datetime.now(KST).strftime("%Y-%m-%d")
    monkeypatch.setattr("src.research.chart.chart_agent.collect_facts",
                        lambda sid, force=False: {
                            "asof": today,
                            "timeframes": {"1d": {"has_data": True},
                                           "1M": {"has_data": False}}})
    _, status, note = inputs._chart("KR:005930", force=False)
    assert status == "partial" and "1M" in note


# ── the diagnostic has to be readable, or it is not a diagnostic ─────────

def test_phase0_log_line_shows_the_auto_counters(monkeypatch, capsys):
    """`filled` holds one entry per symbol and used to consume the whole
    line, truncating away the counters that say whether the auto book got
    its bars — a diagnostic nobody can see is not one."""
    from src.quant import cycle_job

    monkeypatch.setattr(cycle_job, "_phase0_data", lambda ids: {
        "ok": True, "symbols": 22,
        "filled": {f"KR:{i:06d}": 0 for i in range(22)},   # the line-eater
        "quotes_fresh": 22, "auto_symbols": 53, "auto_bars": 436,
        "auto_failed": [], "auto_quotes": 53})
    monkeypatch.setattr(cycle_job, "run_account", lambda *a, **k: 0)
    monkeypatch.setattr(cycle_job.store, "account_ids", lambda: ["robo"])
    monkeypatch.setattr(cycle_job.store, "trade_mode", lambda a: "live")
    monkeypatch.setattr(cycle_job.store, "tracked_symbols", lambda a: [])

    cycle_job.run_cycle("evening")
    line = next(l for l in capsys.readouterr().out.splitlines() if "[0] data" in l)
    assert "53" in line and "436" in line, f"auto counters missing from: {line}"
    assert "KR:000001" not in line, "the per-symbol dict is back in the line"


def test_phase0_log_line_names_a_failed_symbol(monkeypatch, capsys):
    from src.quant import cycle_job

    monkeypatch.setattr(cycle_job, "_phase0_data", lambda ids: {
        "ok": True, "symbols": 0, "filled": {}, "quotes_fresh": 0,
        "auto_symbols": 2, "auto_bars": 1, "auto_quotes": 1,
        "auto_failed": ["US:BWXT"]})
    monkeypatch.setattr(cycle_job, "run_account", lambda *a, **k: 0)
    monkeypatch.setattr(cycle_job.store, "account_ids", lambda: ["robo"])
    monkeypatch.setattr(cycle_job.store, "trade_mode", lambda a: "live")
    monkeypatch.setattr(cycle_job.store, "tracked_symbols", lambda a: [])

    cycle_job.run_cycle("evening")
    line = next(l for l in capsys.readouterr().out.splitlines() if "[0] data" in l)
    assert "US:BWXT" in line, f"a dead symbol must be named: {line}"
