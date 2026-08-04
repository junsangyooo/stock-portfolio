"""Judge agent — the cross-domain verdict every nightly batch and badge depends on.

Covers the two paths that must not silently rot: the claude-failure fallback
(weighted average of cached domain verdicts) and the happy path's persistence.
"""
from __future__ import annotations

import json

import pytest

from src.agents import verdict as V
from src.agents.judge import evaluate as J

_INPUTS = {
    "data_available": {"chart": True, "fundamental": True, "news": True},
    "cached_verdicts": {
        "chart": {"score": 0.6, "confidence": 0.8, "label": "strong_buy",
                  "date": "2026-08-03", "summary_ko": "추세 양호",
                  "drivers": ["이평 정배열"], "risks": []},
        "fundamental": {"score": -0.2, "confidence": 0.6, "label": "sell",
                        "date": "2026-08-03", "summary_ko": "이익 둔화",
                        "drivers": [], "risks": ["마진 축소"]},
    },
    "holding_context": {"qty": 10},
}

_GOOD = json.dumps({
    "score": 0.3, "confidence": 0.7, "label": "buy", "action_ko": "add",
    "summary_ko": "종합적으로 우호적", "domain_summary": {},
    "cross_reads": [], "drivers": ["a"], "risks": ["b"], "watch": [],
    "time_horizon": "near", "priced_in": "unpriced",
    "methodology": ["m"], "limitations": [],
})


class _StubBackend:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    def call(self, system, user, timeout_s=60, model=None):
        self.calls += 1
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def test_label_vocabulary_is_shared_across_agents():
    """The Judge fuses chart/fundamental/news labels, so all four agents must
    map a score to the same word."""
    from src.agents.chart import evaluate as C
    from src.agents.fundamental import evaluate as F
    from src.agents.news import evaluate as N
    for score in (-1.0, -0.6, -0.5, -0.2, 0.0, 0.2, 0.5, 0.6, 1.0):
        expected = V.label_from_score(score)
        assert (C.label_from_score(score) == F.label_from_score(score)
                == N.label_from_score(score) == J.label_from_score(score)
                == expected)
    assert V.label_from_score(5) == "strong_buy"     # clamped
    assert V.label_from_score(-5) == "strong_sell"


def test_fallback_averages_cached_verdicts_when_claude_dies():
    out = J._fallback_verdict(_INPUTS, "claude CLI exit 1")
    assert out["status"] == "fallback"
    # (0.40*0.6 + 0.35*-0.2) / (0.40+0.35)
    assert out["score"] == pytest.approx((0.4 * 0.6 + 0.35 * -0.2) / 0.75, abs=1e-4)
    assert out["label"] == V.label_from_score(out["score"])
    assert out["confidence"] <= 0.35          # a stopgap must not look confident
    assert out["action_ko"] in ("hold", "watch")
    assert out["cross_reads"] == []
    assert any("실패" in x for x in out["limitations"])


def test_fallback_without_any_cache_is_insufficient():
    out = J._fallback_verdict({"cached_verdicts": {}}, "boom")
    assert out["status"] == "insufficient_data"


def test_judge_retries_once_then_falls_back():
    stub = _StubBackend(RuntimeError("claude CLI timeout (320s)"))
    out = J._judge("KR:005930", "삼성전자", _INPUTS, backend=stub)
    assert stub.calls == 2, "one retry before giving up (design §⑥)"
    assert out["status"] == "fallback"


def test_judge_needs_two_domains():
    thin = {"data_available": {"chart": True, "fundamental": False, "news": False}}
    stub = _StubBackend(_GOOD)
    out = J._judge("KR:005930", "삼성전자", thin, backend=stub)
    assert out["status"] == "insufficient_data"
    assert stub.calls == 0, "must not spend a claude call on thin input"


def test_evaluate_symbol_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(J, "assemble_inputs", lambda sid, name=None: _INPUTS)
    monkeypatch.setattr(V, "analysis_path",
                        lambda kind, sid, d: tmp_path / f"{kind}_{d}.json")
    res = J.evaluate_symbol("KR:005930", name="삼성전자", date="2026-08-04",
                            backend=_StubBackend(_GOOD))
    assert res["kind"] == "judge" and res["label"] == "buy"
    saved = json.loads((tmp_path / "judge_20260804.json").read_text(encoding="utf-8"))
    assert saved["action_ko"] == "add"
    assert saved["holding_context"] == {"qty": 10}
    assert "cached_verdicts" not in saved, "full inputs must not be echoed back"
