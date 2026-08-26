"""Tests for theme → symbols reverse lookup.

The masking pass is the part worth pinning: without it every mention of
"한화에어로스페이스" also scores "한화", and the discovery agent gets handed a
parent company it was never told about.
"""
from __future__ import annotations

from src.agents.news.reverse import _count_masked, _strip_html


def test_longer_name_wins_and_shorter_one_inside_it_is_not_counted():
    entries = [("한화에어로스페이스", "KR:012450"), ("한화", "KR:000880")]
    hits = _count_masked("한화에어로스페이스 수주 확대", entries)
    assert hits == {"KR:012450": 1}


def test_standalone_short_name_still_counts():
    entries = [("한화에어로스페이스", "KR:012450"), ("한화", "KR:000880")]
    corpus = "한화에어로스페이스 수주, 한화 지분 매각"
    hits = _count_masked(corpus, entries)
    assert hits == {"KR:012450": 1, "KR:000880": 1}


def test_repeated_mentions_accumulate():
    entries = [("삼성전자", "KR:005930")]
    hits = _count_masked("삼성전자 실적. 삼성전자 목표주가 상향.", entries)
    assert hits["KR:005930"] == 2


def test_no_match_returns_empty():
    assert _count_masked("반도체 업황 회복", [("삼성전자", "KR:005930")]) == {}


def test_strip_html_flattens_rss_summary():
    raw = '<div class="md"><p>SK하이닉스</p>  <br/>HBM</div>'
    assert _strip_html(raw) == " SK하이닉스 HBM "


def test_naive_timestamp_is_made_aware():
    # Feeds mix offsets with bare local timestamps; a naive one anywhere in the
    # list made the sort raise "can't compare offset-naive and offset-aware".
    from src.agents.news.reverse import _published_dt
    aware = _published_dt({"published": "2026-08-26T10:00:00+00:00"})
    naive = _published_dt({"published": "2026-08-26T10:00:00"})
    assert aware.tzinfo is not None
    assert naive.tzinfo is not None
    assert sorted([aware, naive])  # must not raise


def test_fetched_at_is_the_fallback():
    from src.agents.news.reverse import _published_dt
    dt = _published_dt({"published": None, "fetched_at": "2026-08-01T06:07:14+09:00"})
    assert dt.year == 2026 and dt.tzinfo is not None
