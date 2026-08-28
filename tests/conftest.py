"""Shared test fixtures.

The symbol book (data/market.db) is a single file rather than a tree of
per-symbol parquets, so a test that writes to it without redirection would
write to the real one. This points every test at a throwaway file — autouse,
because "remember to request the fixture" is exactly the kind of thing that
gets forgotten in the test that corrupts production data.

It patches ``core.paths`` rather than the store module so importing the
fixture does not drag in the whole market package (and its pykrx dependency).
"""
from __future__ import annotations

import pytest

import src.core.paths as paths


@pytest.fixture(autouse=True)
def market_db(tmp_path, monkeypatch):
    """Fresh empty market.db per test."""
    db_path = tmp_path / "market.db"
    monkeypatch.setattr(paths, "MARKET_DB_PATH", db_path)
    monkeypatch.setattr(paths, "ensure_dirs", lambda: None)
    return db_path
