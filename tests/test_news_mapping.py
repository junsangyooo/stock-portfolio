"""Tests for ETF -> theme name-rule mapping (deterministic, L1).

Individual-stock mapping uses the LLM (non-deterministic) and is exercised
via a stub backend, not asserted on exact themes.
"""
from __future__ import annotations

import pytest

from src.agents.news.catalog import load_catalog
from src.agents.news.mapping import map_etf_by_name, tag_stock_by_llm


@pytest.fixture(scope="module")
def cat():
    return load_catalog()


@pytest.mark.parametrize("name,expected_subset", [
    ("TIGER 코리아휴머노이드로봇산업", {"robotics"}),
    ("ACE 미국우주테크액티브", {"space"}),
    ("KoAct 바이오헬스케어액티브", {"bio"}),
    ("TIGER 미국S&P500", {"macro"}),
    ("TIGER KRX300", {"macro"}),
    ("ACE 코스닥150", {"macro"}),
    ("1Q 미국메디컬AI", {"ai_sw", "bio"}),       # 메디컬AI->ai_sw, 메디컬->bio
    ("RISE 현대차고정피지컬AI", {"robotics", "ai_sw"}),  # 피지컬AI->robotics, AI->ai_sw
])
def test_etf_name_rule_mapping(cat, name, expected_subset):
    got = set(map_etf_by_name(name, cat))
    assert expected_subset.issubset(got), f"{name}: got {got}"


def test_a_sector_etf_does_not_pick_up_the_backbone(cat):
    """Index ETFs map to macro by name; a sector ETF should not."""
    assert "macro" not in map_etf_by_name("TIGER 코리아휴머노이드로봇산업", cat)


def test_etf_unknown_returns_empty(cat):
    # a made-up name with no matching keyword -> needs human review
    assert map_etf_by_name("XYZ 알수없는상품명123", cat) == []


class _StubBackend:
    """Returns a canned JSON tagging result regardless of prompt."""
    def __init__(self, payload: str):
        self._payload = payload

    def call(self, system, user, timeout_s=60, model=None):
        return self._payload


def test_tag_stock_by_llm_parses_and_filters(cat):
    stub = _StubBackend(
        '{"primary": ["battery"], "related": ["ev", "not_a_real_theme"]}'
    )
    res = tag_stock_by_llm("에코프로비엠", "KR", cat, backend=stub)
    assert res["primary"] == ["battery"]
    # unknown themes dropped, known kept
    assert "ev" in res["related"]
    assert "not_a_real_theme" not in res["related"]
