"""The scorecard — a stored analysis graded against what the price did.

Every prompt change from here on gets compared against these numbers, so the
grading rules themselves have to be pinned: what counts as a hit, what counts
as pending, and which coordinate the window reached.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from src.core import paths
from src.core.kst import KST
from src.market.store import ohlcv_store
from src.market.source.base import OHLCVBar
from src.research.analyze import score
from src.research.verdict import KIND

START = datetime(2026, 9, 1, tzinfo=KST)


def _store(sid: str, day: str, *, label="buy", confidence=0.6,
           invalidation=90.0, target=120.0, counter="이 결론이 틀린다면",
           confirm=("종가 120 돌파",), status="ok") -> None:
    d = paths.ANALYSIS_DIR / KIND / sid.replace(":", "_")
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{day.replace('-', '')}.json").write_text(json.dumps({
        "symbol_id": sid, "date": day, "status": status, "label": label,
        "confidence": confidence,
        "counter": {"case_ko": counter, "what_would_show_it": []},
        "outlook": {"scenarios": [{"case": "bull", "confirm": list(confirm),
                                   "invalidate": []}]},
        "levels": {"current": 100.0,
                   "invalidation": {"price": invalidation},
                   "targets": [{"price": target}] if target else []},
    }, ensure_ascii=False), encoding="utf-8")


def _bars(sid: str, closes: list[float], *, high=None, low=None) -> None:
    ohlcv_store.write(sid, [
        OHLCVBar(ts=int((START + timedelta(days=i)).timestamp()),
                 open=c, high=high[i] if high else c,
                 low=low[i] if low else c, close=c, volume=1000.0)
        for i, c in enumerate(closes)])


# ── direction ───────────────────────────────────────────────────────────────

def test_a_buy_that_went_up_is_a_hit_and_one_that_fell_is_not():
    _store("KR:UP", "2026-09-01")
    _bars("KR:UP", [100, 101, 102, 103, 104, 110])
    _store("KR:DOWN", "2026-09-01", label="buy")
    _bars("KR:DOWN", [100, 99, 98, 97, 96, 90])
    out = score.report(horizons=[5])["horizons"][5]
    assert out["graded"] == 2 and out["hit_rate"] == 0.5
    assert out["by_label"]["buy"]["n"] == 2


def test_a_sell_is_graded_the_other_way_round():
    _store("KR:FALL", "2026-09-01", label="sell")
    _bars("KR:FALL", [100, 99, 98, 97, 96, 90])
    assert score.report(horizons=[5])["horizons"][5]["hit_rate"] == 1.0


def test_neutral_is_right_while_the_price_stays_put():
    _store("KR:FLAT", "2026-09-01", label="neutral")
    _bars("KR:FLAT", [100, 100, 101, 100, 99, 101])
    assert score.report(horizons=[5])["horizons"][5]["hit_rate"] == 1.0
    _store("KR:MOVED", "2026-09-01", label="neutral")
    _bars("KR:MOVED", [100, 101, 102, 103, 104, 115])     # +15%
    assert score.report(horizons=[5])["horizons"][5]["hit_rate"] == 0.5


def test_an_answer_the_market_has_not_finished_is_pending_not_wrong():
    _store("KR:YOUNG", "2026-09-01")
    _bars("KR:YOUNG", [100, 101])                          # only 1 day after
    out = score.report(horizons=[5])["horizons"][5]
    assert out["graded"] == 0 and out["pending"] == 1 and out["hit_rate"] is None


def test_confidence_buckets_are_what_calibration_is_read_from():
    _store("KR:SURE", "2026-09-01", confidence=0.85)
    _bars("KR:SURE", [100, 101, 102, 103, 104, 110])
    _store("KR:UNSURE", "2026-09-01", confidence=0.25)
    _bars("KR:UNSURE", [100, 99, 98, 97, 96, 90])
    buckets = score.report(horizons=[5])["horizons"][5]["by_confidence"]
    assert buckets["0.8"]["hit_rate"] == 1.0
    assert buckets["0.2"]["hit_rate"] == 0.0


# ── coordinates ─────────────────────────────────────────────────────────────

def test_the_window_reports_which_coordinate_it_reached():
    _store("KR:TGT", "2026-09-01")
    _bars("KR:TGT", [100, 105, 110, 115, 118, 121])
    _store("KR:STOP", "2026-09-01")
    _bars("KR:STOP", [100, 98, 95, 93, 91, 89])
    _store("KR:NONE", "2026-09-01")
    _bars("KR:NONE", [100, 101, 99, 100, 101, 100])
    first = score.report(horizons=[5])["horizons"][5]["levels_first"]
    assert first == {"target": 1, "stop": 1, "neither": 1}


def test_a_window_that_touched_both_says_so_rather_than_guessing():
    """Daily bars do not carry the order of the two touches."""
    _store("KR:BOTH", "2026-09-01")
    _bars("KR:BOTH", [100, 100, 100, 100, 100, 100],
          high=[100, 125, 100, 100, 100, 100], low=[100, 100, 85, 100, 100, 100])
    assert score.report(horizons=[5])["horizons"][5]["levels_first"] == {"both": 1}


# ── the contract ────────────────────────────────────────────────────────────

def test_the_contract_counters_find_the_thin_answers():
    _store("KR:THIN", "2026-09-01", counter="위험은 있다"[:5], target=None,
           invalidation=None, confirm=("실적 개선 확인",))
    _bars("KR:THIN", [100, 101, 102, 103, 104, 110])
    c = score.report(horizons=[5])["contract"]
    assert c["counter_thin"] == 1 and c["no_invalidation"] == 1
    assert c["no_targets"] == 1
    assert c["conds"] == 1 and c["conds_checkable"] == 0


def test_a_facts_only_result_is_counted_but_never_graded():
    _store("KR:BLANK", "2026-09-01", status="facts_only")
    _bars("KR:BLANK", [100, 101, 102, 103, 104, 110])
    out = score.report(horizons=[5])
    assert out["analyses"] == {"ok": 0, "facts_only": 1}
    assert out["horizons"][5]["graded"] == 0


def test_since_cuts_the_history_short():
    _store("KR:OLD", "2026-09-01")
    _store("KR:NEW", "2026-09-10")
    assert score.report(since="2026-09-05")["analyses"]["ok"] == 1


# ── the baseline ────────────────────────────────────────────────────────────

def test_a_falling_tape_is_not_skill():
    """Every `sell` is right when everything falls. Edge is hit rate minus how
    often the market did that anyway — the only number worth comparing."""
    for i in range(4):
        _store(f"KR:F{i}", "2026-09-01", label="sell")
        _bars(f"KR:F{i}", [100, 99, 98, 97, 96, 90])
    out = score.report(horizons=[5])["horizons"][5]
    assert out["hit_rate"] == 1.0
    assert out["baseline"] == {"up": 0.0, "down": 1.0, "flat": 0.0}
    assert out["by_label"]["sell"]["edge"] == 0.0


def test_calling_the_one_that_rose_in_a_falling_tape_is_edge():
    for i in range(3):
        _store(f"KR:D{i}", "2026-09-01", label="sell")
        _bars(f"KR:D{i}", [100, 99, 98, 97, 96, 90])
    _store("KR:U", "2026-09-01", label="buy")
    _bars("KR:U", [100, 101, 103, 105, 108, 112])
    out = score.report(horizons=[5])["horizons"][5]
    assert out["baseline"]["up"] == 0.25
    assert out["by_label"]["buy"] == {"n": 1, "hit_rate": 1.0,
                                      "mean_ret_pct": 12.0, "edge": 0.75}
