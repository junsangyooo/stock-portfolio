"""Unit tests for the sector tag cache (no network)."""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point the paths module at a temp dir, then reload the store."""
    import src.core.paths as paths
    monkeypatch.setattr(paths, "SECTOR_DIR", tmp_path / "sector")
    monkeypatch.setattr(paths, "sector_path",
                        lambda m, c: paths.SECTOR_DIR / m.upper() / f"{c}.json")
    import src.market.store.sector_store as ss
    importlib.reload(ss)
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
