"""Indicator plugins — deterministic pure functions: (df, tf) -> IndicatorFact.

Four categories: trend / momentum / volatility / volume. Each module exposes an
`INDICATORS` list of callables with the uniform signature

    def f(df: pd.DataFrame, tf: str) -> IndicatorFact

where `df` is a single-timeframe OHLCV frame (cols: ts, open, high, low, close,
volume), already look-ahead-guarded by the engine. Indicators report an
OBJECTIVE state + value (no buy/sell judgment); the downstream claude -p
judgment agent interprets them via the harness rules.

No LLM, no I/O — given the same frame an indicator always returns the same fact.
"""
from __future__ import annotations

from . import trend, momentum, volatility, volume

# All indicator callables across the four categories.
ALL_INDICATORS = (
    trend.INDICATORS
    + momentum.INDICATORS
    + volatility.INDICATORS
    + volume.INDICATORS
)
