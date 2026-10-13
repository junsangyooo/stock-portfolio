"""Unit tests for the sector tag cache (no network).

The autouse `market_db` fixture in conftest already redirects the store at a
throwaway database, so these only need the module.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def store():
    import src.market.store.sector_store as ss
    return ss


def test_read_missing_returns_none(store):
    assert store.read("KR:005930") is None
    assert store.is_fresh("KR:005930") is False


def test_write_then_read(store):
    rec = store.write("KR:005930", "Technology", "Semiconductors")
    assert rec["sector"] == "Technology"
    got = store.read("KR:005930")
    assert got["industry"] == "Semiconductors"
    assert got["symbol_id"] == "KR:005930"
    assert store.is_fresh("KR:005930") is True


def test_empty_sector_not_fresh(store):
    store.write("US:XYZ", None, None)
    assert store.is_fresh("US:XYZ") is False     # no usable sector
