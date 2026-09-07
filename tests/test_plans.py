"""매매 계획 노트 — store CRUD and the per-account boundary.

A plan carries a target price and size for a symbol, so it is account data:
the server-wide /api session gate is not enough, and a session for account A
must not read, edit or delete account B's notes (§10.1).

The SQLite DB is redirected to a tmp file so tests never touch data/.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import portfolio_api
from src.core import security
from src.portfolio.store import account_store, db as pdb, plan_store


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(pdb, "DB_PATH", str(tmp_path / "plans.db"))
    monkeypatch.setattr(pdb, "_initialized", False)
    monkeypatch.setattr(security, "_SECRET_PATH", tmp_path / ".session_secret",
                        raising=False)
    for aid in ("alice", "bob"):
        account_store.create(aid, aid, "GENERAL")
        account_store.set_pin_hash(aid, security.hash_pin(aid, "123456"))

    app = FastAPI()
    app.include_router(portfolio_api.router)
    return TestClient(app)


def _cookie(aid="alice"):
    return {security.COOKIE_PREFIX + aid: security.make_session_token(aid)}


def _body(**kw):
    b = {"symbol_id": "KR:012450", "name": "한화에어로스페이스",
         "side": "buy", "price": 1000000, "qty": 5}
    b.update(kw)
    return b


def _create(client, aid="alice", **kw):
    r = client.post(f"/api/accounts/{aid}/plans", json=_body(**kw),
                    cookies=_cookie(aid))
    assert r.status_code == 200, r.text
    return r.json()["plan"]


# ── store ────────────────────────────────────────────────────────────

def test_store_roundtrip(client):
    p = plan_store.create("alice", symbol_id="KR:005930", name="삼성전자",
                          side="sell", price=95000, qty=10, note="목표 도달")
    assert p["side"] == "sell" and p["price"] == 95000 and p["qty"] == 10
    assert plan_store.list_all("alice")[0]["plan_id"] == p["plan_id"]

    p2 = plan_store.update("alice", p["plan_id"], price=99000)
    assert p2["price"] == 99000
    assert p2["qty"] == 10, "an unset field must be left alone"

    assert plan_store.delete("alice", p["plan_id"]) is True
    assert plan_store.list_all("alice") == []


def test_store_is_scoped_by_account(client):
    p = plan_store.create("alice", symbol_id="KR:005930", name="삼성전자",
                          side="buy", price=70000, qty=1)
    # bob owns nothing here — every path must miss, not fall through to alice's
    assert plan_store.get("bob", p["plan_id"]) is None
    assert plan_store.update("bob", p["plan_id"], price=1) is None
    assert plan_store.delete("bob", p["plan_id"]) is False
    assert plan_store.get("alice", p["plan_id"])["price"] == 70000


def test_plans_die_with_the_account(client):
    plan_store.create("alice", symbol_id="KR:005930", name="삼성전자",
                      side="buy", price=70000, qty=1)
    account_store.delete("alice")
    assert plan_store.list_all("alice") == []


# ── routes ───────────────────────────────────────────────────────────

def test_crud_over_http(client):
    plan = _create(client)
    assert plan["name"] == "한화에어로스페이스"

    r = client.patch(f"/api/accounts/alice/plans/{plan['plan_id']}",
                     json={"qty": 8, "note": "분할 2차"}, cookies=_cookie())
    assert r.status_code == 200
    assert r.json()["plan"]["qty"] == 8
    assert r.json()["plan"]["price"] == 1000000

    assert len(client.get("/api/accounts/alice/plans",
                          cookies=_cookie()).json()["plans"]) == 1

    r = client.delete(f"/api/accounts/alice/plans/{plan['plan_id']}",
                      cookies=_cookie())
    assert r.status_code == 200
    assert client.get("/api/accounts/alice/plans",
                      cookies=_cookie()).json()["plans"] == []


def test_another_account_cannot_touch_the_note(client):
    plan = _create(client, aid="alice")
    pid = plan["plan_id"]
    # bob holds a valid session — for HIS account, which must not reach alice's
    assert client.get("/api/accounts/alice/plans",
                      cookies=_cookie("bob")).status_code == 403
    assert client.patch(f"/api/accounts/alice/plans/{pid}", json={"qty": 1},
                        cookies=_cookie("bob")).status_code == 403
    assert client.delete(f"/api/accounts/alice/plans/{pid}",
                         cookies=_cookie("bob")).status_code == 403
    # and a plan id from another account reads as absent, not as someone else's
    assert client.get("/api/accounts/bob/plans",
                      cookies=_cookie("bob")).json()["plans"] == []
    assert client.delete(f"/api/accounts/bob/plans/{pid}",
                         cookies=_cookie("bob")).status_code == 404


def test_no_session_is_refused(client):
    assert client.get("/api/accounts/alice/plans").status_code == 403


@pytest.mark.parametrize("bad", [
    {"side": "hold"}, {"price": 0}, {"price": -1}, {"qty": 0}, {"qty": -3},
])
def test_rejects_nonsense(client, bad):
    r = client.post("/api/accounts/alice/plans", json=_body(**bad),
                    cookies=_cookie())
    assert r.status_code == 400


def test_missing_plan_is_404(client):
    assert client.patch("/api/accounts/alice/plans/999", json={"qty": 1},
                        cookies=_cookie()).status_code == 404
    assert client.delete("/api/accounts/alice/plans/999",
                         cookies=_cookie()).status_code == 404


def test_chat_plan_block_example_is_accepted_by_the_api(client):
    """The chat prompt hands the model a literal example block.

    The model never writes — it emits that block and the browser posts it back
    through this route. If either side drifts the hand-off breaks silently, so
    the example itself is the contract and has to round-trip.
    """
    import json
    import re

    from src.api import chat

    m = re.search(r"```plan\n(.*?)```", chat.SYSTEM, re.S)
    assert m, "the plan block contract vanished from the chat system prompt"
    plans = json.loads(m.group(1))
    assert plans, "the example must show at least one plan"
    for p in plans:
        r = client.post("/api/accounts/alice/plans", json=p, cookies=_cookie())
        assert r.status_code == 200, r.text
        assert r.json()["plan"]["side"] == p["side"]


def test_plan_contract_reaches_an_ongoing_thread(client):
    """SYSTEM is only sent on a thread's first turn.

    Everything after that leans on the copy the CLI session already holds, so
    a conversation opened before the hand-off existed would never learn the
    format and "노트에 넣어줘" would quietly do nothing in it.
    """
    from src.api import chat

    sys_first, _ = chat._build("alice", "안녕", seeded=False)
    sys_later, user_later = chat._build("alice", "노트에 넣어줘", seeded=True)

    assert "```plan" in sys_first
    assert sys_later == "", "a resumed session must not be re-seeded"
    assert "```plan" in user_later


# ── proposals ────────────────────────────────────────────────────────
# What an agent wrote for a human book. It waits: every executor matches on
# `armed`, so until a person answers it, nothing can fill it.

def _propose(aid="alice", **kw):
    import time
    now = int(time.time())
    with pdb.connect() as c:
        cur = c.execute(
            "INSERT INTO plans (account_id, symbol_id, name, side, price, qty,"
            " note, status, source, created_at, updated_at)"
            " VALUES (?, 'KR:005930', '삼성전자', 'buy', 71000, 3,"
            " '[agent 2026-09-07] 진입', 'proposed', ?, ?, ?)",
            (aid, kw.get("source", "agent"), now, now))
        c.commit()
        return cur.lastrowid


def test_a_proposal_is_not_a_candidate_for_a_trade(client):
    """trade_store matches armed plans; a proposal must not be picked up by
    recording a fill."""
    from src.portfolio.store import trade_store
    _propose()
    r = trade_store.record("alice", symbol_id="KR:005930", side="buy", qty=3,
                           price=71000, name="삼성전자", currency="KRW")
    assert r["plan"] is None and r["plan_candidates"] == []


def test_approving_a_proposal_arms_it(client):
    pid = _propose()
    r = client.patch(f"/api/accounts/alice/plans/{pid}",
                     json={"status": "armed"}, cookies=_cookie())
    assert r.status_code == 200 and r.json()["plan"]["status"] == "armed"


def test_rejecting_a_proposal_abandons_it(client):
    pid = _propose()
    r = client.patch(f"/api/accounts/alice/plans/{pid}",
                     json={"status": "abandoned"}, cookies=_cookie())
    assert r.status_code == 200 and r.json()["plan"]["status"] == "abandoned"


def test_a_proposal_takes_no_other_edit(client):
    pid = _propose()
    r = client.patch(f"/api/accounts/alice/plans/{pid}",
                     json={"price": 60000}, cookies=_cookie())
    assert r.status_code == 400


def test_nothing_becomes_a_proposal(client):
    p = _create(client)
    r = client.patch(f"/api/accounts/alice/plans/{p['plan_id']}",
                     json={"status": "proposed"}, cookies=_cookie())
    assert r.status_code == 400


def test_a_persons_own_plan_is_not_approvable_by_this_door(client):
    """decide() only touches source='agent'. A user-written row in `proposed`
    would be a bug elsewhere; it must not be closed here regardless."""
    pid = _propose(source="user")
    r = client.patch(f"/api/accounts/alice/plans/{pid}",
                     json={"status": "armed"}, cookies=_cookie())
    assert r.status_code == 404


def test_another_account_cannot_approve_it(client):
    pid = _propose("alice")
    r = client.patch(f"/api/accounts/alice/plans/{pid}",
                     json={"status": "armed"}, cookies=_cookie("bob"))
    assert r.status_code == 403


def test_the_plan_list_says_who_wrote_each_one(client):
    _propose()
    _create(client)
    r = client.get("/api/accounts/alice/plans", cookies=_cookie())
    assert {p["source"] for p in r.json()["plans"]} == {"agent", "user"}
