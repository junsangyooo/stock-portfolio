"""Tests for the fundamental domain's deterministic assembly.

The per-domain judgment was removed on 2026-09-01 — the three domains meet
inside one call now (tests/test_analyze.py).
"""
from __future__ import annotations

import json

import pytest

from src.agents.fundamental import evaluate as ev


class _StubBackend:
    """Returns a canned JSON payload; records the prompts it received."""

    def __init__(self, payload: dict):
        self.payload = payload
        self.system = None
        self.user = None

    def call(self, system, user, timeout_s=150, model=None):
        self.system = system
        self.user = user
        return json.dumps(self.payload, ensure_ascii=False)


_GOOD = {
    "score": 0.42,
    "summary_ko": "돈을 잘 벌고 재무도 튼튼하지만 주가는 비싼 편입니다.",
    "axes": {"value": 35, "profitability": 80, "growth": 70,
             "stability": 85, "shareholder_return": 50},
    "axis_notes": {"value": "싼가? 비쌈", "profitability": "버는가? 우수",
                   "growth": "자라는가? 양호", "stability": "버티는가? 튼튼",
                   "shareholder_return": "돌려주나? 보통"},
    "metric_explanations": [
        {"key": "per", "label": "PER", "value": 44.3, "plain": "이익의 44배 가격, 비쌈"},
    ],
    "drivers": ["메모리 슈퍼사이클 매출 +69%"],
    "risks": ["밸류 부담"],
    "red_flags": [],
    "sector_assumed": "반도체",
    "confidence": 0.7,
    "methodology": ["B1", "C2"],
}

_INPUTS = {
    "valuation": {"per": 44.3, "pbr": 3.99},
    "financials_latest": {"revenue": 1000, "net_income": 100},
    "latest_period": "2024Q3",
    "n_periods": 9,
    "derived": {"roe": 0.13, "operating_margin": 0.2},
    "data_available": {"valuation": True, "financials": True, "sector": True},
}
