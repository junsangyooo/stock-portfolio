"""Tests for the news theme catalog (L2) loader + integrity validation."""
from __future__ import annotations

import pytest

from src.agents.news.catalog import load_catalog


@pytest.fixture(scope="module")
def cat():
    return load_catalog()


def test_loads_the_field_taxonomy(cat):
    core = {
        "battery", "ai_sw", "bio", "semiconductor", "robotics",
        "space", "fintech", "shipbuilding", "adv_manufacturing", "macro",
    }
    assert core.issubset(set(cat.themes))


def test_every_theme_is_a_primary_candidate_except_the_backbone(cat):
    """The old active/shadow split is gone — collection covers every field, so
    a theme nobody holds is still a theme a symbol can be tagged into."""
    got = {t.theme_id for t in cat.primary_candidates()}
    assert got == set(cat.themes) - {"macro"}


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


def test_every_theme_has_a_korean_name(cat):
    for t in cat.themes.values():
        assert t.name_kr, f"{t.theme_id} has no name_kr"


def test_macro_is_the_only_backbone(cat):
    on = {t.theme_id for t in cat.themes.values() if t.always_on}
    assert on == {"macro"}
