"""Auto-adjust 예수금 (cash balance) on recorded buy/sell trades.

Covers:
  - balance_store.adjust: signed delta, row autocreate, overdraft allowed
  - POST /api/accounts/{id}/holdings with trade context → cash deducted (buy)
    / credited (sell), currency resolved from symbol_id
  - DELETE .../holdings/{sid}?sell_price&sell_qty → final sellout credits cash
  - plain add / direct-edit (no trade context) → cash untouched

The SQLite DB is redirected to a tmp file so tests never touch data/.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import portfolio_api
from src.portfolio.store import (
    account_store, balance_store, holding_store, db as pdb,
)
from src.api.portfolio_api import require_account


@pytest.fixture
def client(tmp_path, monkeypatch):
    # isolated DB
    monkeypatch.setattr(pdb, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(pdb, "_initialized", False)

    # universe lookup: KR symbol settles KRW, US settles USD
    def fake_get(sid):
        ccy = "USD" if sid.startswith("US:") else "KRW"
        return {"currency": ccy, "market": sid.split(":")[0]}
    monkeypatch.setattr(portfolio_api.uni_search, "get", fake_get)

    # skip real market backfill (network)
    monkeypatch.setattr(portfolio_api.backfill, "daily", lambda sid: 0)

    app = FastAPI()
    app.include_router(portfolio_api.router)
    # bypass PIN auth
    app.dependency_overrides[require_account] = lambda: "acc1"

    account_store.create("acc1", "Test", "GENERAL", base_currency="KRW")
    return TestClient(app)


# ── store-level ──────────────────────────────────────────────────────

def test_adjust_creates_row_and_sums(tmp_path, monkeypatch):
    monkeypatch.setattr(pdb, "DB_PATH", str(tmp_path / "s.db"))
    monkeypatch.setattr(pdb, "_initialized", False)
    account_store.create("a", "n", "GENERAL")
    assert balance_store.adjust("a", "KRW", 1000.0) == 1000.0
    assert balance_store.adjust("a", "KRW", -250.0) == 750.0
    # overdraft allowed (no block)
    assert balance_store.adjust("a", "KRW", -2000.0) == -1250.0
    # zero delta is a no-op returning current
    assert balance_store.adjust("a", "KRW", 0.0) == -1250.0


# ── buy deducts cash ─────────────────────────────────────────────────

def test_buy_deducts_cash(client):
    balance_store.set_amount("acc1", "KRW", 1_000_000)
    r = client.post("/api/accounts/acc1/holdings", json={
        "symbol_id": "KR:005930",
        "avg_cost": 70000, "qty": 10,
        "trade_side": "buy", "trade_price": 70000, "trade_qty": 10,
    })
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["cash_currency"] == "KRW"
    assert d["cash_balance"] == 1_000_000 - 700_000
    assert balance_store.get_all("acc1")["KRW"] == 300_000


# ── sell credits cash ────────────────────────────────────────────────

def test_sell_credits_cash(client):
    balance_store.set_amount("acc1", "KRW", 300_000)
    holding_store.upsert("acc1", "KR:005930", avg_cost=70000, qty=10)
    r = client.post("/api/accounts/acc1/holdings", json={
        "symbol_id": "KR:005930",
        "avg_cost": 70000, "qty": 6,   # sold 4
        "trade_side": "sell", "trade_price": 80000, "trade_qty": 4,
    })
    assert r.status_code == 200, r.text
    assert r.json()["cash_balance"] == 300_000 + 320_000
    assert balance_store.get_all("acc1")["KRW"] == 620_000


# ── US symbol settles in USD ─────────────────────────────────────────

def test_us_trade_uses_usd(client):
    balance_store.set_amount("acc1", "USD", 5000)
    r = client.post("/api/accounts/acc1/holdings", json={
        "symbol_id": "US:AAPL",
        "avg_cost": 200, "qty": 5,
        "trade_side": "buy", "trade_price": 200, "trade_qty": 5,
    })
    d = r.json()
    assert d["cash_currency"] == "USD"
    assert d["cash_balance"] == 5000 - 1000
    # KRW untouched
    assert "KRW" not in balance_store.get_all("acc1")


# ── no trade context → cash untouched (plain add / edit) ─────────────

def test_plain_add_does_not_touch_cash(client):
    balance_store.set_amount("acc1", "KRW", 500_000)
    r = client.post("/api/accounts/acc1/holdings", json={
        "symbol_id": "KR:005930", "avg_cost": 70000, "qty": 10,
    })
    d = r.json()
    assert d["cash_currency"] is None and d["cash_balance"] is None
    assert balance_store.get_all("acc1")["KRW"] == 500_000


def test_edit_mode_does_not_touch_cash(client):
    balance_store.set_amount("acc1", "KRW", 500_000)
    holding_store.upsert("acc1", "KR:005930", avg_cost=70000, qty=10)
    # direct edit overrides avg/qty, no trade_side
    r = client.post("/api/accounts/acc1/holdings", json={
        "symbol_id": "KR:005930", "avg_cost": 65000, "qty": 12,
    })
    assert r.json()["cash_balance"] is None
    assert balance_store.get_all("acc1")["KRW"] == 500_000


# ── full sellout via DELETE with sell params credits cash ────────────

def test_full_sellout_delete_credits_cash(client):
    balance_store.set_amount("acc1", "KRW", 100_000)
    holding_store.upsert("acc1", "KR:005930", avg_cost=70000, qty=5)
    r = client.delete(
        "/api/accounts/acc1/holdings/KR:005930"
        "?sell_price=80000&sell_qty=5")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["deleted"] == 1
    assert d["cash_balance"] == 100_000 + 400_000
    assert balance_store.get_all("acc1")["KRW"] == 500_000
    # holding gone
    assert holding_store.list_by_account("acc1") == []


def test_delete_without_sell_params_keeps_cash(client):
    balance_store.set_amount("acc1", "KRW", 100_000)
    holding_store.upsert("acc1", "KR:005930", avg_cost=70000, qty=5)
    r = client.delete("/api/accounts/acc1/holdings/KR:005930")
    d = r.json()
    assert d["deleted"] == 1
    assert d["cash_currency"] is None
    assert balance_store.get_all("acc1")["KRW"] == 100_000
