"""ChartFactCollector — the quantitative pipeline's entry point.

It EXTRACTS facts only; it does NOT judge. For each timeframe (daily / weekly /
monthly, look-ahead-guarded) it gathers every indicator's objective state +
value, the volume-spike flag, and the structure detections (S/R, fib, pivots,
trendlines, candle/chart patterns, gaps). The output JSON is the chart domain
of src/research/analyze, which reads it next to fundamental and news with
chart_rules.md as this domain's dictionary (field meanings, when an indicator
lies — not a rulebook).

No aggregate score, no buy/sell label, no LLM here — 100% deterministic facts.
"""
from __future__ import annotations

from datetime import datetime, timezone

from ...core.kst import KST
from typing import Optional

import pandas as pd

from ...market.store import chart_store, ohlcv_store
from . import engine, structure
from .indicators import ALL_INDICATORS
from .indicators.volume import volume_spike

ENGINE_VERSION = "chart-facts-v1"


def _facts_for_tf(frame: pd.DataFrame, tf: str) -> dict:
    facts = [f(frame, tf) for f in ALL_INDICATORS]
    return {
        "indicators": [f.to_dict() for f in facts],
        "volume_spike": volume_spike(frame, tf),
        "has_data": any(f.has_data for f in facts),
        "asof": engine.last_closed_date(frame),
    }


def collect_facts(symbol_id: str, now: Optional[datetime] = None,
                  daily_df: Optional[pd.DataFrame] = None,
                  persist: bool = True, force: bool = False) -> dict:
    """Collect all chart FACTS for a symbol across timeframes (+ structure).

    Deterministic — no LLM, no judgment. Reuses today's stored row unless
    `force`: the facts for a closed bar cannot change, so running the
    comprehensive analysis three times in one day still computes the chart
    once. `daily_df` injects data without a read (tests / batch).
    """
    if persist and not force and daily_df is None:
        cached = _cached_today(symbol_id, now)
        if cached is not None:
            return cached
    daily = daily_df if daily_df is not None else ohlcv_store.read(symbol_id)

    timeframes = {}
    for tf in engine.TIMEFRAMES:
        framed = engine.to_timeframe(daily, tf)
        closed = engine.drop_incomplete_bar(framed, tf, now=now)
        timeframes[tf] = _facts_for_tf(closed, tf)

    struct = structure.detect_structure(daily) if (daily is not None and not daily.empty) else {}
    asof = timeframes["1d"]["asof"]

    out = {
        "symbol_id": symbol_id,
        "kind": "chart",
        "asof": asof,
        "timeframes": timeframes,
        "structure": struct,
        "engine_version": ENGINE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat() + "Z",
    }

    if persist and asof:
        chart_store.write(symbol_id, asof,
                          {"timeframes": timeframes, "structure": struct},
                          ENGINE_VERSION)
        out["_stored"] = True

    return out


def _cached_today(symbol_id: str, now: Optional[datetime]) -> Optional[dict]:
    """Stored facts if they are already for the newest closed bar.

    Compares against the last stored bar rather than the calendar date, so a
    weekend or a holiday reuses Friday's row instead of recomputing it.
    """
    row = chart_store.read(symbol_id)
    if not row or row.get("engine_version") != ENGINE_VERSION:
        return None
    last = ohlcv_store.last_ts(symbol_id)
    if not last:
        return None
    newest = datetime.fromtimestamp(last, tz=KST).strftime("%Y-%m-%d")
    if row["date"] != newest:
        return None
    return {
        "symbol_id": symbol_id, "kind": "chart", "asof": row["date"],
        "timeframes": row.get("timeframes", {}),
        "structure": row.get("structure", {}),
        "engine_version": row["engine_version"],
        "generated_at": row["built_at"], "_cached": True,
    }
