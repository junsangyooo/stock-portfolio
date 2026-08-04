"""Tests for the fundamental agent's judge + assembly (stubbed LLM, no network)."""
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


def test_judge_parses_and_shapes():
    stub = _StubBackend(_GOOD)
    v = ev._judge("KR:005930", "삼성전자", _INPUTS, backend=stub)
    assert v["score"] == pytest.approx(0.42)
    assert v["label"] == "buy"                  # 0.1 < 0.42 ≤ 0.5
    assert v["summary_ko"].startswith("돈을")
    assert v["axes"]["profitability"] == 80
    assert v["axis_notes"]["value"] == "싼가? 비쌈"
    assert v["confidence"] == 0.7
    assert "B1" in v["methodology"]
    # the rulebook must be embedded in the system prompt
    assert "Piotroski" in stub.system or "F-Score" in stub.system


def test_judge_clamps_score():
    stub = _StubBackend({**_GOOD, "score": 9.9})
    v = ev._judge("KR:005930", "삼성전자", _INPUTS, backend=stub)
    assert v["score"] == 1.0 and v["label"] == "strong_buy"


def test_judge_insufficient_data_no_llm_call():
    """No financials → return a low-confidence verdict WITHOUT calling the LLM."""
    called = {"n": 0}

    class _Spy:
        def call(self, *a, **k):
            called["n"] += 1
            return "{}"

    empty = {"data_available": {"valuation": False, "financials": False}}
    v = ev._judge("KR:000000", "없음", empty, backend=_Spy())
    assert called["n"] == 0                     # never hit the model
    assert v["confidence"] == 0.0
    assert v["score"] is None and v["label"] in ("pending", "neutral")


def test_assemble_display_wraps_inputs_and_series(monkeypatch):
    import src.agents.fundamental.evaluate as mod
    monkeypatch.setattr(mod, "assemble_inputs", lambda sid: dict(_INPUTS))
    series = [{"period": "2024Q2", "revenue": 900},
              {"period": "2024Q3", "revenue": 1000}]
    monkeypatch.setattr(mod.financials_store, "rows_as_dicts",
                        lambda sid, n=0: series)
    out = mod.assemble_display("KR:005930", name="삼성전자")
    assert out["kind"] == "fundamental_facts"
    assert out["name"] == "삼성전자"
    assert out["financials_series"] == series
    assert out["valuation"] == _INPUTS["valuation"]   # inputs merged in
    assert out["data_available"]["financials"] is True


def test_evaluate_symbol_persists(tmp_path, monkeypatch):
    import src.agents.fundamental.evaluate as mod
    import src.agents.verdict as verdict
    monkeypatch.setattr(mod, "assemble_inputs", lambda sid: _INPUTS)
    monkeypatch.setattr(verdict, "analysis_path",
                        lambda kind, sid, d: tmp_path / f"{kind}_{d}.json")
    stub = _StubBackend(_GOOD)
    res = mod.evaluate_symbol("KR:005930", name="삼성전자",
                              date="2026-05-27", backend=stub)
    assert res["kind"] == "fundamental"
    assert res["label"] == "buy"
    assert res["inputs"] == _INPUTS
    out = tmp_path / "fundamental_20260527.json"
    assert out.exists()
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["summary_ko"].startswith("돈을")
