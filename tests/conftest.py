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
import requests

import src.core.paths as paths
from src.core import notify
from src.market.source import kis_source
from src.core.llm import ClaudeCliBackend


@pytest.fixture(autouse=True)
def temp_books(tmp_path, monkeypatch):
    """Fresh empty books (market, news, portfolio), analysis tree and session
    secret per test."""
    monkeypatch.setattr(paths, "MARKET_DB_PATH", tmp_path / "market.db")
    monkeypatch.setattr(paths, "NEWS_DB_PATH", tmp_path / "news.db")
    # Stored analyses are read by existence (analyze.read globs the folder), so
    # a suite run on a host that has actually analysed something answers from
    # production output: test_api_analysis asserted exists=False for KR:005930
    # and failed on Oracle alone, where 20260901.json sits in the real tree.
    monkeypatch.setattr(paths, "ANALYSIS_DIR", tmp_path / "analysis")
    # The usage ledger is billing evidence, not test output: a suite run on
    # the server would otherwise append rows for calls nobody made.
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(paths, "ensure_dirs", lambda: None)
    monkeypatch.setattr(paths, "CHAT_CWD", tmp_path / "chat_sandbox")
    # The account book and the session secret are bound when their own
    # modules import, so they are redirected there. Without it a test that
    # opened the app wrote data/portfolio.db and read or wrote
    # data/.session_secret — on the server, the live ones.
    from src.portfolio import security
    from src.market.store import quote_store
    from src.portfolio.store import db as pdb
    from src.research.analyze import batch_job
    from src.universe import builder
    monkeypatch.setattr(pdb, "DB_PATH", tmp_path / "portfolio.db")
    monkeypatch.setattr(pdb, "_initialized", False)
    # …and every module that imported ensure_dirs by name, which otherwise
    # still creates the real data/ tree.
    for mod in (pdb, quote_store, batch_job, builder):
        monkeypatch.setattr(mod, "ensure_dirs", lambda: None)
    monkeypatch.setattr(security, "_SECRET_FILE", tmp_path / ".session_secret")
    monkeypatch.setattr(security, "_secret_cache", None)
    return tmp_path


@pytest.fixture(autouse=True)
def no_live_broker(tmp_path, monkeypatch):
    """Every HTTP call made through ``requests`` raises unless a test stubs it
    itself.

    Local secrets are live — the KIS keys the US screener signs with among
    them — and a test that reaches a real API spends a real token or worse (a
    broker client once registered real orders this way). Patched on the
    ``requests`` module itself, so a test's own
    ``monkeypatch.setattr(module.requests, "get", …)`` still takes precedence.
    The KIS token cache points at a throwaway file too.
    """
    def _blocked(method, url=None, *a, **k):
        raise RuntimeError(f"test reached the live broker: {method} {url}")

    monkeypatch.setattr(requests, "request", _blocked)
    monkeypatch.setattr(requests, "post",
                        lambda url, *a, **k: _blocked("POST", url))
    monkeypatch.setattr(requests, "get",
                        lambda url, *a, **k: _blocked("GET", url))
    monkeypatch.setattr(kis_source, "_TOKEN_PATH", tmp_path / ".kis_token.json")
    # Discord too: no test may post to the real channel. A test that wants to
    # count messages patches ``discord.send`` itself; this only unplugs the URL.
    monkeypatch.setattr(notify, "_url", lambda: None)


@pytest.fixture(autouse=True)
def no_claude_cli(monkeypatch):
    """No test may spawn ``claude -p`` — a call is minutes of real model time
    on the subscription. Best-effort paths that swallow the error (theme
    auto-map, warm batches) keep working; a test that needs an answer stubs
    ``ClaudeCliBackend.call`` itself, which takes precedence."""
    def _blocked(self, *a, **k):
        raise RuntimeError("test reached claude CLI")
    monkeypatch.setattr(ClaudeCliBackend, "call", _blocked)


@pytest.fixture
def market_db(temp_books):
    return temp_books / "market.db"


@pytest.fixture
def news_db(temp_books):
    return temp_books / "news.db"
