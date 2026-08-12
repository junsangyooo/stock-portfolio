"""Tests for the daily digest — per-lane pre-rank quota and overview parsing.

The quota exists because a flat leading-first ranking let arXiv fill all 40
slots on a busy day, so the digest had no market news left to summarize.
"""
from __future__ import annotations

import json

from src.agents.news.digest import _rank_and_cap, make_daily_digest


class _StubBackend:
    """Returns a canned LLM response; records the prompt it was given."""
    name = "stub"

    def __init__(self, payload: str):
        self.payload = payload
        self.prompt = None

    def call(self, system, prompt, timeout_s=None, model=None):
        self.prompt = prompt
        return self.payload


def _items(lane: str, n: int, start: int = 0) -> list[dict]:
    return [{"lead_lag": lane, "title": f"{lane}-{i}", "url": f"https://x/{lane}/{i}",
             "source_id": lane, "summary": "",
             "fetched_at": f"2026-08-12T{(start + i) % 24:02d}:00:00+09:00"}
            for i in range(n)]


def test_rank_and_cap_keeps_news_when_arxiv_floods():
    # 60 arXiv + 10 news: the old ranking kept 40 arXiv and zero news
    raw = _items("leading", 60) + _items("lagging", 10)
    kept = _rank_and_cap(raw, cap=40)
    assert len(kept) == 40
    lanes = [r["lead_lag"] for r in kept]
    assert lanes.count("lagging") == 10, "every available news item must survive"
    assert lanes.count("leading") <= 34, "arXiv must not crowd out other lanes"


def test_rank_and_cap_refills_unused_quota():
    # only one lane present -> quota (6) must not shrink the result below cap
    kept = _rank_and_cap(_items("leading", 50), cap=40)
    assert len(kept) == 40


def test_rank_and_cap_noop_under_cap():
    raw = _items("lagging", 5)
    assert _rank_and_cap(raw, cap=40) == raw


def test_digest_parses_overview():
    raw = _items("lagging", 3)
    be = _StubBackend(json.dumps({
        "overview": ["HBM 공급 이슈로 가격 반등", "  ", "장비 수요 확대"],
        "selected": [{"idx": 0, "summary": "요약", "importance": 4, "novelty": True}],
        "dropped_dup": 1,
    }))
    d = make_daily_digest("semiconductor", "반도체", raw, "2026-08-12", backend=be)
    assert d["overview"] == ["HBM 공급 이슈로 가격 반등", "장비 수요 확대"]
    assert len(d["items"]) == 1
    assert "overview" in be.prompt, "the prompt must ask for the overview"


def test_digest_survives_missing_or_bad_overview():
    raw = _items("lagging", 3)
    for payload in ('{"selected": [{"idx": 0, "summary": "s", "importance": 3}]}',
                    '{"overview": "not a list", '
                    '"selected": [{"idx": 0, "summary": "s", "importance": 3}]}'):
        d = make_daily_digest("x", "X", raw, "2026-08-12",
                              backend=_StubBackend(payload))
        assert d["overview"] == []
        assert len(d["items"]) == 1, "a bad overview must not cost us the items"


def test_digest_empty_input_has_overview_key():
    d = make_daily_digest("x", "X", [], "2026-08-12")
    assert d["overview"] == [] and d["items"] == []
