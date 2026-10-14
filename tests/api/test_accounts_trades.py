"""거래 기록 — POST /api/accounts/{id}/trades writes a `trades` row and, in
the same transaction, moves holdings (qty · weighted avg) and cash
(ops notes §3.1). The human book keeps no plans.

The SQLite DB is redirected to a tmp file so tests never touch data/.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import accounts
from src.portfolio import security
from src.portfolio.store import (account_store, balance_store, db as pdb,
                                 holding_store, trade_store)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(pdb, "DB_PATH", str(tmp_path / "trades.db"))
    monkeypatch.setattr(pdb, "_initialized", False)
    monkeypatch.setattr(accounts.uni_search, "get", lambda sid: {
        "currency": "USD" if sid.startswith("US:") else "KRW",
        "name_kr": "이름 " + sid, "market": sid.split(":")[0]})
    monkeypatch.setattr(accounts.gap_filler, "fill_one", lambda sid: 0)
    for aid in ("alice", "bob"):
        account_store.create(aid, aid, "GENERAL")
        account_store.set_pin_hash(aid, security.hash_pin(aid, "123456"))
    app = FastAPI()
    app.include_router(accounts.router)
    return TestClient(app)


def _cookie(aid="alice"):
    return {security.COOKIE_PREFIX + aid: security.make_session_token(aid)}


def _trade(client, aid="alice", **kw):
    body = {"symbol_id": "KR:005930", "side": "buy", "qty": 10, "price": 70000}
    body.update(kw)
    return client.post(f"/api/accounts/{aid}/trades", json=body,
                       cookies=_cookie(aid))


def _holding(aid="alice", sid="KR:005930"):
    return next(h for h in holding_store.list_by_account(aid)
                if h["symbol_id"] == sid)


# ── holdings · cash ───────────────────────────────────────────────────

def test_buy_creates_holding_and_moves_cash(client):
    balance_store.set_many("alice", {"KRW": 1_000_000})
    r = _trade(client, fee=500)
    assert r.status_code == 200, r.text
    d = r.json()
    h = _holding()
    assert h["qty"] == 10 and h["avg_cost"] == 70000
    assert d["holding"] == {"qty": 10, "avg_cost": 70000, "removed": False}
    assert d["cash_currency"] == "KRW"
    assert d["cash_balance"] == 1_000_000 - 700_000 - 500
    assert balance_store.get_all("alice")["KRW"] == 299_500
    rows = trade_store.list_by_account("alice")
    assert len(rows) == 1 and rows[0]["side"] == "buy" and rows[0]["fee"] == 500


def test_second_buy_averages_cost(client):
    _trade(client, qty=10, price=70000)
    r = _trade(client, qty=10, price=80000)
    assert r.status_code == 200, r.text
    h = _holding()
    assert h["qty"] == 20 and h["avg_cost"] == 75000


def test_sell_reduces_keeps_avg_and_credits_cash(client):
    balance_store.set_many("alice", {"KRW": 0})
    _trade(client, qty=10, price=70000)
    r = _trade(client, side="sell", qty=4, price=80000, fee=100)
    assert r.status_code == 200, r.text
    h = _holding()
    assert h["qty"] == 6 and h["avg_cost"] == 70000
    assert balance_store.get_all("alice")["KRW"] == -700_000 + 320_000 - 100


def test_full_sell_removes_the_row(client):
    """A sell-out deletes the card; the trade rows keep the history."""
    _trade(client, qty=5, price=70000)
    r = _trade(client, side="sell", qty=5, price=80000)
    assert r.status_code == 200, r.text
    assert r.json()["holding"] == {"qty": 0, "avg_cost": None, "removed": True}
    assert holding_store.list_by_account("alice") == []
    assert len(trade_store.list_by_account("alice")) == 2


def test_oversell_rejected(client):
    _trade(client, qty=5, price=70000)
    r = _trade(client, side="sell", qty=6, price=80000)
    assert r.status_code == 400


def test_sell_without_holding_rejected(client):
    r = _trade(client, side="sell", qty=1, price=80000)
    assert r.status_code == 400


def test_us_symbol_settles_usd(client):
    balance_store.set_many("alice", {"USD": 5000})
    r = _trade(client, symbol_id="US:AAPL", qty=5, price=200)
    assert r.json()["cash_currency"] == "USD"
    assert balance_store.get_all("alice") == {"USD": 4000}


def test_buy_after_full_sell_creates_a_fresh_row(client):
    """The sell-out took the row with it, so the next buy opens a new card."""
    _trade(client)
    _trade(client, side="sell", qty=10, price=71000)
    assert holding_store.list_by_account("alice") == []
    _trade(client, qty=5, price=60000)
    h = _holding()
    assert h["qty"] == 5 and h["avg_cost"] == 60000


# ── validation ────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [{"qty": 0}, {"qty": -1}, {"price": 0},
                                 {"side": "hold"}, {"fee": -1}])
def test_bad_input_400(client, bad):
    assert _trade(client, **bad).status_code == 400


def test_float_dust_sell_is_flat(client):
    holding_store.upsert("alice", "US:AAPL", avg_cost=100, qty=0.1 + 0.2)   # 0.30000000000000004
    r = _trade(client, symbol_id="US:AAPL", side="sell", qty=0.3, price=200)
    assert r.status_code == 200, r.text
    assert r.json()["holding"] == {"qty": 0, "avg_cost": None, "removed": True}


def test_wrong_account_cookie_403(client):
    r = client.post("/api/accounts/alice/trades",
                    json={"symbol_id": "KR:005930", "side": "buy",
                          "qty": 1, "price": 1}, cookies=_cookie("bob"))
    assert r.status_code == 403
    r = client.get("/api/accounts/alice/trades", cookies=_cookie("bob"))
    assert r.status_code == 403


def test_list_trades_newest_first(client):
    _trade(client, traded_at=100)
    _trade(client, traded_at=200, side="sell", qty=1, price=1)
    r = client.get("/api/accounts/alice/trades", cookies=_cookie())
    assert r.status_code == 200
    rows = r.json()["trades"]
    assert [t["traded_at"] for t in rows] == [200, 100]
    assert rows[0]["name"] == "이름 KR:005930"


def test_every_account_route_requires_account():
    """Every /{account_id}/... route except the PIN login carries
    require_account — the server-wide gate only checks for *some* session."""
    from src.api.accounts import require_account
    missing = []
    for route in accounts.router.routes:
        if "{account_id}" not in route.path or route.path.endswith("/auth"):
            continue
        deps = [d.call for d in route.dependant.dependencies]
        if require_account not in deps:
            missing.append(f"{sorted(route.methods)} {route.path}")
    assert not missing, missing


# ── flat position · sort order · unknown cost ─────────────────────────

def test_partial_sell_keeps_the_row(client):
    holding_store.upsert("alice", "KR:005930", avg_cost=70000, qty=5,
                         note="배당 목적")
    r = _trade(client, side="sell", qty=2, price=80000)
    assert r.status_code == 200, r.text
    assert r.json()["holding"] == {"qty": 3, "avg_cost": 70000, "removed": False}
    h = _holding()
    assert h["qty"] == 3 and h["note"] == "배당 목적"


def test_buy_onto_a_watch_row_opens_it(client):
    """A qty-0 watch row (added without a trade) becomes a position on the
    first buy and keeps its note."""
    holding_store.upsert("alice", "KR:005930", note="지켜보는 중")
    r = _trade(client)
    assert r.status_code == 200, r.text
    h = _holding()
    assert h["qty"] == 10 and h["note"] == "지켜보는 중"


def test_a_trade_answers_without_plan_fields_and_ignores_a_plan_id(client):
    d = _trade(client, plan_id=7).json()
    assert "plan" not in d and "plan_candidates" not in d
    assert "plan_id" not in d["trade"]          # the column is gone (2026-10-14)


def _seed_history(client):
    # 2026-09-01 00:00 KST = 1788188400; 09-02 = +86400; 09-03 = +172800
    _trade(client, traded_at=1788188400 + 3600)                            # 09-01
    _trade(client, traded_at=1788188400 + 86400 + 3600, symbol_id="KR:000660")  # 09-02
    _trade(client, traded_at=1788188400 + 172800 + 3600)                   # 09-03


def _history(client, **params):
    r = client.get("/api/accounts/alice/trades", params=params, cookies=_cookie())
    assert r.status_code == 200, r.text
    return r.json()["trades"]


def test_trades_filter_by_symbol(client):
    _seed_history(client)
    rows = _history(client, symbol_id="KR:000660")
    assert [t["symbol_id"] for t in rows] == ["KR:000660"]


def test_trades_filter_by_date_range_inclusive_kst(client):
    _seed_history(client)
    assert len(_history(client, since="2026-09-02")) == 2
    assert len(_history(client, until="2026-09-02")) == 2
    rows = _history(client, since="2026-09-02", until="2026-09-02")
    assert [t["traded_at"] for t in rows] == [1788188400 + 86400 + 3600]
    # a trade at 23:59 KST on the `until` day is still inside
    _trade(client, traded_at=1788188400 + 172800 + 86400 - 1)              # 09-03 23:59:59
    assert len(_history(client, since="2026-09-03", until="2026-09-03")) == 2


def test_trades_limit_is_clamped(client):
    _seed_history(client)
    assert len(_history(client, limit=2)) == 2
    assert len(_history(client, limit=5000)) == 3


@pytest.mark.parametrize("bad", [{"since": "2026-9-1"}, {"until": "yesterday"},
                                 {"since": "2026-13-01"}])
def test_trades_malformed_date_is_400(client, bad):
    r = client.get("/api/accounts/alice/trades", params=bad, cookies=_cookie())
    assert r.status_code == 400


def test_trade_opened_holding_sorts_after_existing_cards(client):
    holding_store.upsert("alice", "KR:000660", avg_cost=1, qty=1)
    holding_store.reorder("alice", ["KR:000660"])          # sort_order 1..N
    assert _trade(client).status_code == 200
    order = [h["symbol_id"] for h in holding_store.list_by_account("alice")]
    assert order == ["KR:000660", "KR:005930"]


def test_buy_onto_unknown_cost_starts_at_the_buy_price(client):
    holding_store.upsert("alice", "KR:005930", avg_cost=None, qty=10)   # cost unknown
    r = _trade(client, qty=10, price=70000)
    assert r.status_code == 200, r.text
    assert _holding()["avg_cost"] == 70000        # not 35000 (old shares at 0)


def test_side_case_is_normalised_everywhere(client, monkeypatch):
    filled = []
    monkeypatch.setattr(accounts.gap_filler, "fill_one",
                        lambda sid: filled.append(sid))
    r = _trade(client, side="BUY")
    assert r.status_code == 200, r.text
    assert trade_store.list_by_account("alice")[0]["side"] == "buy"
    assert filled == ["KR:005930"]                # fresh-position tail ran


def test_record_holds_the_write_lock_from_the_first_read(tmp_path, monkeypatch):
    """BEGIN IMMEDIATE: a second writer cannot slip between the holdings
    read and the write. Simulated by an outside connection that already
    holds the reserved lock — record() must wait/fail, never read past it."""
    import sqlite3
    monkeypatch.setattr(pdb, "DB_PATH", str(tmp_path / "lock.db"))
    monkeypatch.setattr(pdb, "_initialized", False)
    account_store.create("alice", "alice", "GENERAL")
    holding_store.upsert("alice", "KR:005930", avg_cost=70000, qty=10)
    other = sqlite3.connect(pdb.DB_PATH)
    other.execute("BEGIN IMMEDIATE")
    real_connect = sqlite3.connect
    monkeypatch.setattr(sqlite3, "connect",
                        lambda path, **kw: real_connect(path, **{**kw, "timeout": 0.2}))
    with pytest.raises(sqlite3.OperationalError):
        trade_store.record("alice", symbol_id="KR:005930", side="sell",
                           qty=10, price=80000, currency="KRW")
    other.rollback()
    assert _holding()["qty"] == 10 and "KRW" not in balance_store.get_all("alice")

