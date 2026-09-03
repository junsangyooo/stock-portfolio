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
from src.core.notify import discord
from src.quant.broker import toss


@pytest.fixture(autouse=True)
def temp_books(tmp_path, monkeypatch):
    """Fresh empty market.db and news.db per test."""
    monkeypatch.setattr(paths, "MARKET_DB_PATH", tmp_path / "market.db")
    monkeypatch.setattr(paths, "NEWS_DB_PATH", tmp_path / "news.db")
    monkeypatch.setattr(paths, "ensure_dirs", lambda: None)
    return tmp_path


@pytest.fixture(autouse=True)
def no_live_broker(tmp_path, monkeypatch):
    """Every Toss HTTP call raises unless a test stubs it itself.

    Local secrets make the broker live; a test that reaches it registers real
    orders (it happened). Patched through ``toss.requests`` so a test's own
    ``monkeypatch.setattr(toss.requests, "request", …)`` still takes precedence.
    """
    def _blocked(method, url=None, *a, **k):
        raise RuntimeError(f"test reached the live broker: {method} {url}")

    monkeypatch.setattr(toss.requests, "request", _blocked)
    monkeypatch.setattr(toss.requests, "post",
                        lambda url, *a, **k: _blocked("POST", url))
    monkeypatch.setattr(toss.requests, "get",
                        lambda url, *a, **k: _blocked("GET", url))
    monkeypatch.setattr(toss, "_TOKEN_PATH", tmp_path / ".toss_token.json")
    # Discord too: no test may post to the real channel. A test that wants to
    # count messages patches ``discord.send`` itself; this only unplugs the URL.
    monkeypatch.setattr(discord, "_url", lambda: None)


@pytest.fixture
def market_db(temp_books):
    return temp_books / "market.db"


@pytest.fixture
def news_db(temp_books):
    return temp_books / "news.db"
