"""The hot read paths do not touch the disk twice for the same bytes.

  - analyze.summary() parses a stored result once per (path, mtime)
  - the page shell (html + theme + fragments) is read once per mtime
  - one flush for a batch of quotes; one query for an account's sparklines
"""
from __future__ import annotations

import os
import pathlib

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.core.paths as paths
from src.api import market as market_api
from src.dashboard_web import server
from src.market.source.base import OHLCVBar, Quote
from src.market.store import ohlcv_store, quote_store
from src.portfolio import service
from src.research import analyze
from src.research.analyze import read as aread


@pytest.fixture
def read_text_calls(monkeypatch):
    calls = []
    real = pathlib.Path.read_text

    def counting(self, *a, **k):
        calls.append(str(self))
        return real(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "read_text", counting)
    return calls


# ── analyze.summary ─────────────────────────────────────────────────────────

def _store(tmp_path, sid, stem, label):
    d = tmp_path / "analysis" / aread.KIND / sid.replace(":", "_")
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{stem}.json"
    p.write_text('{"label": "%s", "score": 0.3}' % label, encoding="utf-8")
    return p


def test_summary_reads_each_body_once(tmp_path, monkeypatch, read_text_calls):
    monkeypatch.setattr(paths, "ANALYSIS_DIR", tmp_path / "analysis")
    aread._read_at.cache_clear()
    p = _store(tmp_path, "KR:005930", "20260901", "buy")
    ids = ["KR:005930", "KR:000660"]          # the second was never analyzed

    first = analyze.summary(ids)
    n = len(read_text_calls)
    assert first["KR:005930"]["label"] == "buy"
    assert first["KR:000660"]["date"] is None
    assert n == 1

    assert analyze.summary(ids) == first
    assert len(read_text_calls) == n, "second call re-read the body"

    # A rewrite (new mtime) is a miss: edits show without a restart.
    p.write_text('{"label": "sell"}', encoding="utf-8")
    os.utime(p, ns=(p.stat().st_atime_ns, p.stat().st_mtime_ns + 1_000_000))
    assert analyze.summary(ids)["KR:005930"]["label"] == "sell"
    assert len(read_text_calls) == n + 1


def test_latest_hands_out_a_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "ANALYSIS_DIR", tmp_path / "analysis")
    aread._read_at.cache_clear()
    _store(tmp_path, "US:AAPL", "20260901", "buy")
    analyze.latest("US:AAPL")["label"] = "mutated"
    assert analyze.latest("US:AAPL")["label"] == "buy"


# ── page shell ──────────────────────────────────────────────────────────────

def test_second_page_request_reads_nothing(read_text_calls):
    c = TestClient(server.app)
    assert c.get("/").status_code == 200
    n = len(read_text_calls)
    r = c.get("/")
    assert r.status_code == 200
    assert len(read_text_calls) == n, read_text_calls[n:]
    assert 'id="theme-vars"' in r.text and 'id="shared-js"' in r.text


def test_nonce_is_still_per_response():
    c = TestClient(server.app)
    a = c.get("/").headers["content-security-policy"]
    b = c.get("/").headers["content-security-policy"]
    assert a != b


def test_read_follows_the_mtime(tmp_path):
    p = tmp_path / "f.txt"
    p.write_text("one")
    assert server._read(p) == "one"
    p.write_text("two")
    os.utime(p, ns=(p.stat().st_atime_ns, p.stat().st_mtime_ns + 1_000_000))
    assert server._read(p) == "two"


# ── quotes ──────────────────────────────────────────────────────────────────

@pytest.fixture
def quote_flushes(tmp_path, monkeypatch):
    monkeypatch.setattr(quote_store, "_FILE", tmp_path / "last.json")
    monkeypatch.setattr(quote_store, "_cache", {})
    monkeypatch.setattr(quote_store, "_loaded", True)
    flushes = []
    real = quote_store._flush_locked
    monkeypatch.setattr(quote_store, "_flush_locked",
                        lambda: flushes.append(1) or real())
    return flushes


def test_set_many_flushes_once(quote_flushes):
    quote_store.set_many([Quote(f"KR:{i}", 1.0, 1.0, 0) for i in range(3)])
    assert len(quote_flushes) == 1
    assert quote_store.get("KR:2") is not None
    quote_store.set_many([])
    assert len(quote_flushes) == 1


def test_quotes_route_stores_a_batch_with_one_flush(quote_flushes, monkeypatch):
    class Src:
        def get_quote(self, sid):
            return Quote(sid, 10.0, 9.0, 1_900_000_000)

    monkeypatch.setattr(market_api, "for_symbol", lambda sid: Src())
    app = FastAPI()
    app.include_router(market_api.router)
    body = TestClient(app).get(
        "/api/market/quotes?symbols=KR:1,KR:2,US:A").json()["quotes"]
    assert len(quote_flushes) == 1
    assert all(not v["stale"] for v in body.values()) and len(body) == 3


# ── sparklines ──────────────────────────────────────────────────────────────

def _bars(n, base):
    return [OHLCVBar(ts=86400 * i, open=1, high=1, low=1, close=base + i,
                     volume=0) for i in range(n)]


def test_read_closes_is_one_query(monkeypatch):
    ohlcv_store.write("KR:A", _bars(5, 100))
    ohlcv_store.write("KR:B", _bars(2, 200))
    opened = []
    real = ohlcv_store.connect

    def counting():
        opened.append(1)
        return real()

    monkeypatch.setattr(ohlcv_store, "connect", counting)
    out = ohlcv_store.read_closes(["KR:A", "KR:B", "KR:NONE"], 3)
    assert len(opened) == 1
    assert out == {"KR:A": [102.0, 103.0, 104.0], "KR:B": [200.0, 201.0]}
    assert ohlcv_store.read_closes([], 3) == {}


def test_aggregate_draws_sparklines_from_one_read(monkeypatch):
    sids = ["KR:A", "KR:B"]
    monkeypatch.setattr(service.account_store, "get",
                        lambda aid: {"account_id": aid, "base_currency": "KRW"})
    monkeypatch.setattr(service.holding_store, "list_by_account",
                        lambda aid: [{"symbol_id": s, "qty": 1, "avg_cost": 1}
                                     for s in sids])
    monkeypatch.setattr(service.balance_store, "get_all", lambda aid: {})
    monkeypatch.setattr(service.snapshot_store, "maybe_snapshot_today",
                        lambda *a, **k: None)
    monkeypatch.setattr(service.uni_search, "get", lambda sid: {})
    monkeypatch.setattr(service.quote_store, "get", lambda sid, **kw: None)
    asked = []
    monkeypatch.setattr(service.ohlcv_store, "read_closes",
                        lambda ids, n: asked.append(list(ids)) or {"KR:A": [1.0, 2.0]})
    monkeypatch.setattr(service.ohlcv_store, "read",
                        lambda *a, **k: pytest.fail("per-holding read"))

    out = service.aggregate("acc")
    assert asked == [sids]
    assert [h["spark"] for h in out["holdings"]] == [[1.0, 2.0], []]
