"""Shared test fixtures.

The symbol book (data/market.db) and the news book (data/news.db) are single
files rather than trees of per-symbol/per-theme files, so a test that writes to
one without redirection would write to the real one. This points every test at
throwaway files — autouse, because "remember to request the fixture" is exactly
the kind of thing that gets forgotten in the test that corrupts production
data.

It patches ``core.paths`` rather than the store module so importing the
fixture does not drag in the whole market package (and its pykrx dependency).
"""
from __future__ import annotations

import pytest

import src.core.paths as paths


@pytest.fixture(autouse=True)
def temp_books(tmp_path, monkeypatch):
    """Fresh empty market.db and news.db per test."""
    monkeypatch.setattr(paths, "MARKET_DB_PATH", tmp_path / "market.db")
    monkeypatch.setattr(paths, "NEWS_DB_PATH", tmp_path / "news.db")
    monkeypatch.setattr(paths, "ensure_dirs", lambda: None)
    return tmp_path


@pytest.fixture
def market_db(temp_books):
    return temp_books / "market.db"


@pytest.fixture
def news_db(temp_books):
    return temp_books / "news.db"
