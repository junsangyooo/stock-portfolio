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


# Both of these were live false positives on the semiconductor theme.

def test_hangul_name_inside_a_compound_is_rejected():
    entries = [("디바이스", "KR:187870")]
    assert _count_masked("반도체 사업부인 디바이스솔루션(DS)부문", entries) == {}
    assert _count_masked("DX(디바이스경험) 부문 수익성", entries) == {}


def test_latin_name_inside_a_longer_token_is_rejected():
    entries = [("DGI", "KR:099520")]
    assert _count_masked("월덱스, DGIST와 산학협력 MOU 체결", entries) == {}
    assert _count_masked("DGI, 신규 수주 공시", entries) == {"KR:099520": 1}


def test_korean_particles_still_count_as_a_mention():
    entries = [("삼성전자", "KR:005930")]
    for suffix in ("가", "는", "의", "에서", "와", "만", "측", ""):
        corpus = f"{'삼성전자'}{suffix} 실적 발표"
        assert _count_masked(corpus, entries) == {"KR:005930": 1}, suffix


def test_name_preceded_by_hangul_is_rejected():
    entries = [("디바이스", "KR:187870")]
    assert _count_masked("차세대디바이스 시장", entries) == {}


def test_short_latin_name_in_the_korean_field_is_floored_as_latin():
    # KR:160550's name_kr is "NEW". Picking the floor by field gave it the
    # 3-char Hangul floor, and it scored on every "NEW" in English prose.
    from src.agents.news.reverse import _name_entries
    import src.universe.search as uni

    uni._items = [
        {"symbol_id": "KR:160550", "name_kr": "NEW", "name_en": None},
        {"symbol_id": "KR:005930", "name_kr": "삼성전자", "name_en": None},
    ]
    uni._loaded_at = float("inf")
    try:
        names = [n for n, _ in _name_entries()]
    finally:
        uni._items, uni._loaded_at = [], 0.0
    assert "NEW" not in names
    assert "삼성전자" in names
