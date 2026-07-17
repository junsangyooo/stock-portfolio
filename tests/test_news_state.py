"""Tests for L4 digest + state: time-decay (pure) and merge logic (stub LLM)."""
from __future__ import annotations

from src.agents.news.state import (
    decay_recent, empty_state, update_state, _digest_to_developments,
    _dedup_developments,
)
from src.agents.news.digest import make_daily_digest


def test_decay_keeps_within_horizon():
    devs = [
        {"date": "2026-05-24", "point": "today", "weight": 0.8},
        {"date": "2026-05-20", "point": "4d ago", "weight": 0.6},
        {"date": "2026-05-10", "point": "14d ago", "weight": 0.9},  # out of window
    ]
    kept = decay_recent(devs, asof="2026-05-24", horizon_days=7)
    pts = {d["point"] for d in kept}
    assert pts == {"today", "4d ago"}
    assert "14d ago" not in pts


def test_decay_caps_by_weight():
    devs = [{"date": "2026-05-24", "point": f"p{i}", "weight": i / 10}
            for i in range(20)]
    kept = decay_recent(devs, asof="2026-05-24", horizon_days=7, cap=5)
    assert len(kept) == 5
    # highest weights kept
    assert {d["point"] for d in kept} == {"p19", "p18", "p17", "p16", "p15"}


def test_decay_drops_future_and_bad_dates():
    devs = [
        {"date": "2026-05-25", "point": "future", "weight": 1.0},   # after asof
        {"date": "garbage", "point": "bad", "weight": 1.0},
        {"date": "2026-05-23", "point": "ok", "weight": 0.5},
    ]
    kept = decay_recent(devs, asof="2026-05-24")
    assert {d["point"] for d in kept} == {"ok"}


def test_digest_to_developments_weight():
    digest = {"date": "2026-05-24", "items": [
        {"summary": "big news", "importance": 5, "lead_lag": "leading", "url": "u1"},
        {"summary": "minor", "importance": 1, "lead_lag": "lagging", "url": "u2"},
    ]}
    devs = _digest_to_developments(digest)
    assert devs[0]["weight"] == 1.0 and devs[1]["weight"] == 0.2
    assert devs[0]["lead_lag"] == "leading"


def test_dedup_developments_by_url():
    devs = [
        {"url": "https://u/a", "point": "A"},
        {"url": "https://u/a", "point": "A dup"},   # same url -> dropped
        {"url": "https://u/b", "point": "B"},
        {"url": "", "point": "no-url"},
    ]
    out = _dedup_developments(devs)
    assert [d["point"] for d in out] == ["A", "B", "no-url"]


def test_update_state_idempotent_same_day():
    """Re-running the same digest the same day must not duplicate developments."""
    stub_payload = ('{"horizon_summary": "h", "watchlist": [], '
                    '"sentiment": {"score": 0.1, "trend": "flat"}, "last_shift": ""}')
    digest = {"theme": "battery", "date": "2026-05-24", "items": [
        {"summary": "동일 뉴스", "importance": 4, "lead_lag": "news", "url": "https://u/x"},
    ]}

    class _S:
        def call(self, system, user, timeout_s=60, model=None):
            return stub_payload

    s1 = update_state("battery", "배터리", digest, prev_state=empty_state("battery"),
                      date="2026-05-24", backend=_S())
    s2 = update_state("battery", "배터리", digest, prev_state=s1,
                      date="2026-05-24", backend=_S())
    assert len(s2["recent_developments"]) == 1  # not 2


class _StubBackend:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def call(self, system, user, timeout_s=60, model=None):
        self.calls += 1
        return self.payload


def test_make_daily_digest_restores_url_from_idx():
    raw = [
        {"title": "A", "url": "https://u/a", "source_id": "s1", "lead_lag": "leading", "summary": "x"},
        {"title": "B", "url": "https://u/b", "source_id": "s2", "lead_lag": "lagging", "summary": "y"},
    ]
    stub = _StubBackend('{"selected": [{"idx": 1, "summary": "B요약", "importance": 4, "novelty": true}], "dropped_dup": 0}')
    d = make_daily_digest("battery", "배터리", raw, "2026-05-24", backend=stub)
    assert len(d["items"]) == 1
    it = d["items"][0]
    assert it["url"] == "https://u/b" and it["source_id"] == "s2"  # restored from idx
    assert it["summary"] == "B요약" and it["importance"] == 4


def test_make_daily_digest_tolerates_list_importance():
    """Regression: LLM sometimes emits importance as [4]; must not crash."""
    raw = [{"title": "A", "url": "u", "source_id": "s", "lead_lag": "news", "summary": ""}]
    stub = _StubBackend('{"selected": [{"idx": 0, "summary": "x", "importance": [4], "novelty": true}]}')
    d = make_daily_digest("semiconductor", "반도체", raw, "2026-05-27", backend=stub)
    assert len(d["items"]) == 1
    assert d["items"][0]["importance"] == 4  # coerced from [4]


def test_make_daily_digest_tolerates_bad_importance():
    raw = [{"title": "A", "url": "u", "source_id": "s", "lead_lag": "news", "summary": ""}]
    stub = _StubBackend('{"selected": [{"idx": 0, "summary": "x", "importance": "high"}]}')
    d = make_daily_digest("t", "T", raw, "2026-05-27", backend=stub)
    assert d["items"][0]["importance"] == 3  # default fallback


def test_make_daily_digest_drops_bad_idx():
    raw = [{"title": "A", "url": "u", "source_id": "s", "lead_lag": "news", "summary": ""}]
    stub = _StubBackend('{"selected": [{"idx": 9, "summary": "z", "importance": 3}]}')
    d = make_daily_digest("t", "T", raw, "2026-05-24", backend=stub)
    assert d["items"] == []  # idx 9 out of range -> dropped


def test_make_daily_digest_empty_is_no_llm():
    stub = _StubBackend("should not be called")
    d = make_daily_digest("t", "T", [], "2026-05-24", backend=stub)
    assert d["n_raw"] == 0 and d["items"] == [] and stub.calls == 0


def test_update_state_no_recent_skips_llm():
    stub = _StubBackend("unused")
    digest = {"theme": "battery", "date": "2026-05-24", "items": []}
    st = update_state("battery", "배터리", digest, prev_state=empty_state("battery"),
                      backend=stub)
    assert stub.calls == 0  # no developments -> no LLM
    assert st["recent_developments"] == []


def test_update_state_merges_and_calls_llm():
    stub = _StubBackend('{"horizon_summary": "추세 요약", "watchlist": ["이슈1"], '
                        '"sentiment": {"score": -0.3, "trend": "deteriorating"}, '
                        '"last_shift": "2026-05-24: 중립→약세 (공급과잉)"}')
    digest = {"theme": "battery", "date": "2026-05-24", "items": [
        {"summary": "공급과잉 우려", "importance": 4, "lead_lag": "lagging", "url": "u"},
    ]}
    st = update_state("battery", "배터리", digest, prev_state=empty_state("battery"),
                      backend=stub)
    assert stub.calls == 1
    assert st["sentiment"]["score"] == -0.3
    assert st["watchlist"] == ["이슈1"]
    assert len(st["recent_developments"]) == 1
    assert st["updated"] == "2026-05-24"
