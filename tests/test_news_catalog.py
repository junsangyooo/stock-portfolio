"""Tests for the news theme catalog (L2) loader + integrity validation."""
from __future__ import annotations

import pytest

from src.agents.news.catalog import load_catalog


@pytest.fixture(scope="module")
def cat():
    return load_catalog()


def test_loads_and_has_active_themes(cat):
    # the 10 active themes from docs/qualitative-analysis-design.md §1.1
    expected_active = {
        "battery", "ai_sw", "bio", "semiconductor", "robotics",
        "space", "fintech", "shipbuilding", "adv_manufacturing", "macro",
    }
    assert expected_active.issubset(set(cat.themes))
    assert {t.theme_id for t in cat.active_themes()} == expected_active


def test_theme_ids_unique(cat):
    ids = [t.theme_id for t in cat.themes.values()]
    assert len(ids) == len(set(ids))


def test_related_edges_reference_existing_themes(cat):
    """Every related edge must point to a theme that exists in the catalog."""
    for t in cat.themes.values():
        for edge in t.related:
            assert edge["theme"] in cat.themes, (
                f"{t.theme_id} -> unknown related theme '{edge['theme']}'"
            )


def test_related_weights_in_range(cat):
    for t in cat.themes.values():
        for edge in t.related:
            assert 0.0 <= edge["weight"] <= 1.0


def test_active_themes_have_korean_name_and_keywords(cat):
    for t in cat.active_themes():
        assert t.name_kr
        assert t.keywords.get("ko"), f"{t.theme_id} missing ko keywords"


def test_macro_is_always_on(cat):
    assert cat.themes["macro"].always_on is True
    assert cat.themes["macro"].active is True


def test_shadow_themes_are_inactive(cat):
    for t in cat.themes.values():
        if t.shadow:
            assert t.active is False
