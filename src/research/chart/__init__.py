"""Quantitative (chart) analysis — FACT EXTRACTION ONLY.

This package extracts objective technical facts from OHLCV. It does NOT judge
(no buy/sell, no aggregate score). The facts are one of the three domains
src/research/analyze reads in a single call; chart_rules.md is that domain's
dictionary (field meanings, when an indicator lies), not a rulebook.

Modules:
  schema.py        IndicatorFact (value + objective state)
  engine.py        multi-timeframe resample + look-ahead guard
  indicators/      trend / momentum / volatility / volume -> IndicatorFact
  structure/       extrema, S/R, fib, pivots, trendlines, candle/chart patterns, gaps
  facts.py         collect_facts — bundles all facts into one JSON document
"""
