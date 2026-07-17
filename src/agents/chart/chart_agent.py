"""ChartFactCollector — the quantitative pipeline's entry point.

It EXTRACTS facts only; it does NOT judge. For each timeframe (daily / weekly /
monthly, look-ahead-guarded) it gathers every indicator's objective state +
value, the volume-spike flag, and the structure detections (S/R, fib, pivots,
trendlines, candle/chart patterns, gaps). The output JSON is the input for the
later claude -p judgment agent, which applies the harness rules
(docs/chart-judgment-rules.md) to decide buy/sell/risk.

No aggregate score, no buy/sell label, no LLM here — 100% deterministic facts.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

from ...core import paths
from . import engine, structure
from .indicators import ALL_INDICATORS
from .indicators.volume import volume_spike

ENGINE_VERSION = "chart-facts-v1"


def _json_default(o):
    if isinstance(o, np.generic):
        return o.item()
    return str(o)


def _facts_for_tf(frame: pd.DataFrame, tf: str) -> dict:
    facts = [f(frame, tf) for f in ALL_INDICATORS]
    return {
        "indicators": [f.to_dict() for f in facts],
        "volume_spike": volume_spike(frame, tf),
        "has_data": any(f.has_data for f in facts),
        "asof": engine.last_closed_date(frame),
    }


def collect_facts(symbol_id: str, now: Optional[datetime] = None,
                  daily_df: Optional[pd.DataFrame] = None, persist: bool = True) -> dict:
    """Collect all chart FACTS for a symbol across timeframes (+ structure).

    `daily_df` injects data without disk read (tests / batch). Persists to
    data/analysis/chart/{symbol}/{yyyymmdd}.json when `persist` and data exists.
    """
    daily = daily_df if daily_df is not None else engine.load_ohlcv(symbol_id)

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
        p = paths.analysis_path("chart", symbol_id, asof.replace("-", ""))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=_json_default))
        out["_saved"] = str(p)

    return out


def to_json(result: dict) -> str:
    """Serialise a collect_facts result to a JSON string (numpy-safe)."""
    return json.dumps(result, ensure_ascii=False, default=_json_default)
