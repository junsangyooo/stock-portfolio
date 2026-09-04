"""거래 기록 — POST /api/accounts/{id}/trades writes a `trades` row and, in
the same transaction, moves holdings (qty · weighted avg), cash and the
linked plan (ops notes §3.1: a trade row is what transitions plan.status).

The SQLite DB is redirected to a tmp file so tests never touch data/.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import portfolio_api
from src.core import security
from src.portfolio.store import (
    account_store, balance_store, db as pdb, holding_store, plan_store,
    trade_store,
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(pdb, "DB_PATH", str(tmp_path / "trades.db"))
    monkeypatch.setattr(pdb, "_initialized", False)
    monkeypatch.setattr(security, "_SECRET_PATH", tmp_path / ".session_secret",
                        raising=False)
    monkeypatch.setattr(portfolio_api.uni_search, "get", lambda sid: {
        "currency": "USD" if sid.startswith("US:") else "KRW",
        "name_kr": "이름 " + sid, "market": sid.split(":")[0]})
    monkeypatch.setattr(portfolio_api.gap_filler, "fill_one", lambda sid: 0)
    for aid in ("alice", "bob"):
        account_store.create(aid, aid, "GENERAL")
        account_store.set_pin_hash(aid, security.hash_pin(aid, "123456"))
    app = FastAPI()
    app.include_router(portfolio_api.router)
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
    assert d["holding"] == {"qty": 10, "avg_cost": 70000}
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


def test_full_sell_leaves_row_with_zero_qty(client):
    _trade(client, qty=5, price=70000)
    r = _trade(client, side="sell", qty=5, price=80000)
    assert r.status_code == 200, r.text
    assert _holding()["qty"] == 0
    assert r.json()["holding"]["qty"] == 0


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


def test_entry_rationale_written_once(client):
    """entry_* is written when the position opens and never overwritten."""
    p = plan_store.create("alice", symbol_id="KR:005930", name="삼성전자",
                          side="buy", price=70000, qty=10, note="첫 이유")
    _trade(client, plan_id=p["plan_id"])
    assert _holding()["entry_rationale"] == "첫 이유"
    assert _holding()["entry_plan_id"] == p["plan_id"]
    p2 = plan_store.create("alice", symbol_id="KR:005930", name="삼성전자",
                           side="buy", price=60000, qty=10, note="두번째")
    _trade(client, plan_id=p2["plan_id"])
    assert _holding()["entry_rationale"] == "첫 이유"
    assert _holding()["entry_plan_id"] == p["plan_id"]


def test_reentry_after_full_sell_rewrites_entry_context(client):
    """A full sell keeps the row at qty 0; the next buy re-opens the position
    and must carry the new plan's rationale, not the closed-out one's."""
    p = plan_store.create("alice", symbol_id="KR:005930", name="삼성전자",
                          side="buy", price=70000, qty=10, note="첫 이유")
    _trade(client, plan_id=p["plan_id"])
    _trade(client, side="sell", qty=10, price=71000)
    assert _holding()["qty"] == 0
    p2 = plan_store.create("alice", symbol_id="KR:005930", name="삼성전자",
                           side="buy", price=60000, qty=5, note="두번째 이유")
    _trade(client, plan_id=p2["plan_id"], qty=5, price=60000)
    h = _holding()
    assert h["entry_rationale"] == "두번째 이유"
    assert h["entry_plan_id"] == p2["plan_id"]


# ── validation ────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", [{"qty": 0}, {"qty": -1}, {"price": 0},
                                 {"side": "hold"}, {"fee": -1}])
def test_bad_input_400(client, bad):
    assert _trade(client, **bad).status_code == 400


# ── plan transitions ──────────────────────────────────────────────────

def _plan(aid="alice", **kw):
    b = dict(symbol_id="KR:005930", name="삼성전자", side="buy",
             price=70000, qty=10)
    b.update(kw)
    return plan_store.create(aid, **b)


def test_buy_transitions_single_armed_plan(client):
    p = _plan()
    d = _trade(client).json()
    assert d["plan"]["plan_id"] == p["plan_id"]
    assert d["plan"]["status"] == "entered"
    assert plan_store.get("alice", p["plan_id"])["status"] == "entered"
    assert d["plan_candidates"] == []


def test_full_sell_moves_entered_plan_to_done_partial_keeps(client):
    p = _plan()
    _trade(client, qty=10)                                   # → entered
    d = _trade(client, side="sell", qty=4, price=80000).json()
    assert d["plan"] is None
    assert plan_store.get("alice", p["plan_id"])["status"] == "entered"
    d = _trade(client, side="sell", qty=6, price=80000).json()
    assert d["plan"]["status"] == "done"
    assert plan_store.get("alice", p["plan_id"])["status"] == "done"


def test_sell_plan_done_on_full_sell(client):
    _trade(client, qty=10)
    p = _plan(side="sell", price=80000)
    d = _trade(client, side="sell", qty=10, price=80000).json()
    assert d["plan"]["plan_id"] == p["plan_id"]
    assert plan_store.get("alice", p["plan_id"])["status"] == "done"


def test_sell_with_only_armed_buy_plan_transitions_nothing(client):
    holding_store.upsert("alice", "KR:005930", avg_cost=70000, qty=10)
    p = _plan()
    d = _trade(client, side="sell", qty=10, price=80000).json()
    assert d["plan"] is None and d["plan_candidates"] == []
    assert plan_store.get("alice", p["plan_id"])["status"] == "armed"


def test_two_candidates_transition_nothing_and_list_them(client):
    a, b = _plan(), _plan(price=65000)
    r = _trade(client)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["plan"] is None
    assert sorted(d["plan_candidates"]) == sorted([a["plan_id"], b["plan_id"]])
    assert all(plan_store.get("alice", p["plan_id"])["status"] == "armed"
               for p in (a, b))
    assert _holding()["qty"] == 10                          # trade still recorded


def test_explicit_plan_id_wins_over_ambiguity(client):
    a, b = _plan(), _plan(price=65000)
    d = _trade(client, plan_id=b["plan_id"]).json()
    assert d["plan"]["plan_id"] == b["plan_id"]
    assert plan_store.get("alice", a["plan_id"])["status"] == "armed"
    assert plan_store.get("alice", b["plan_id"])["status"] == "entered"


def test_plan_symbol_mismatch_400(client):
    p = _plan(symbol_id="KR:000660", name="하이닉스")
    r = _trade(client, plan_id=p["plan_id"])
    assert r.status_code == 400
    assert trade_store.list_by_account("alice") == []


def test_other_accounts_plan_rejected(client):
    p = _plan(aid="bob")
    r = _trade(client, plan_id=p["plan_id"])
    assert r.status_code == 404
    assert trade_store.list_by_account("alice") == []
    assert plan_store.get("bob", p["plan_id"])["status"] == "armed"


# ── auth ──────────────────────────────────────────────────────────────

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
    from src.api.portfolio_api import require_account
    missing = []
    for route in portfolio_api.router.routes:
        if "{account_id}" not in route.path or route.path.endswith("/auth"):
            continue
        deps = [d.call for d in route.dependant.dependencies]
        if require_account not in deps:
            missing.append(f"{sorted(route.methods)} {route.path}")
    assert not missing, missing
