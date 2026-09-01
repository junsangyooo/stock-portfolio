"""Quantitative (chart) analysis — FACT EXTRACTION ONLY.

This package extracts objective technical facts from OHLCV. It does NOT judge
(no buy/sell, no aggregate score). Judgment is done downstream by the claude -p
judgment agent, which reads the harness guide chart_rules.md and
the facts produced here.

Modules:
  schema.py        IndicatorFact (value + objective state)
  engine.py        multi-timeframe resample + look-ahead guard
  indicators/      trend / momentum / volatility / volume -> IndicatorFact
  structure/       extrema, S/R, fib, pivots, trendlines, candle/chart patterns, gaps
  chart_agent.py   ChartFactCollector — bundles all facts into one JSON document
"""
