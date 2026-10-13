"""Nothing stale may pass as current.

Separate paths let a frozen feed look like a live one, and a freeze ran twelve
days before anyone noticed — every job reported `ok` throughout, because each
layer only ever asked "is there data" and never "how old is it".

  [1] quote_store.get() with no ceiling handed out prices up to 108 days old
      to the account valuation
  [2] the chart input checked bar COUNT, never bar DATE, so an analysis
      computed on stale bars went out stamped with today and status ok

Each test below fails if one of those comes back.
"""
import json
import time
from datetime import datetime, timedelta

import pytest

from src.core.kst import KST
from src.market.store import quote_store
from src.market.source.base import Quote


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
])
def test_every_valuation_path_passes_a_ceiling(module, attr):
    """Whoever turns a quote into money or into a judgment must bound its
    age. The poller may read the cache raw; these may not."""
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
    monkeypatch.setattr("src.research.chart.facts.collect_facts",
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
    monkeypatch.setattr("src.research.chart.facts.collect_facts",
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
    monkeypatch.setattr("src.research.chart.facts.collect_facts",
                        lambda sid, force=False: {
                            "asof": today,
                            "timeframes": {"1d": {"has_data": True},
                                           "1M": {"has_data": False}}})
    _, status, note = inputs._chart("KR:005930", force=False)
    assert status == "partial" and "1M" in note
