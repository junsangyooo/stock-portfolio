"""The paper account — same pipeline as the real one, fills in the ledger.

What must hold: the bootstrap is idempotent, the two books never mix, a met
condition fills at the plan price without a broker call, a buy the cash cannot
cover is rejected, a stop returns the cash, and the API reads either book by
`?account=` while refusing broker-only writes for the paper one.
"""
import re
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.quant.broker as broker
from src.quant.broker import toss
from src.api import quant as qapi
from src.core import security
from src.quant import db as qdb, execute, keeper, plans, store

PAPER = qdb.PAPER_ACCOUNT_ID
PIN = "030220"


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(qdb, "QUANT_DB_PATH", tmp_path / "quant.db")
    monkeypatch.setattr(qdb, "_initialized", False)
    store.create_account("robo", "로보", broker="toss")
    store.set_trade_mode("robo", "live")
    assert store.add_paper_account()
    monkeypatch.setattr(keeper, "market_open", lambda m, now: True)
    monkeypatch.setattr(broker, "fee_rate", lambda market, default=0.0: 0.0015)

    def _no_broker(*a, **k):
        raise AssertionError(f"paper account reached the broker: {a}")
    for fn in ("place_order", "place_conditional", "place_oco", "buyable",
               "balance", "cancel_conditional", "conditional", "quote",
               "pending", "conditionals"):
        monkeypatch.setattr(broker, fn, _no_broker)
    monkeypatch.setattr(toss, "commissions", _no_broker)


def _plan(account, sid="KR:005930", qty=3, price=70000, **over):
    conds = {"entry": {"kind": "below", "price": price},
             "stop": {"kind": "below", "price": 60000},
             "take_profit": {"kind": "above", "price": 80000}}
    kw = dict(symbol_id=sid, name="삼성전자", side="buy", qty=qty, price=price,
              conditions=conds)
    kw.update(over)
    return plans.create(account, **kw)


# ── (a) bootstrap ────────────────────────────────────────────────────────

def test_add_paper_account_is_idempotent():
    assert store.account_ids() == ["robo", PAPER]
    acct = store.account(PAPER)
    assert acct["broker"] == "paper" and acct["kind"] == "auto"
    assert acct["mode"] == "live" and acct["base_currency"] == "KRW"
    assert store.cash(PAPER) == {"USD": 1000.0, "KRW": 1_000_000.0}
    store.set_cash(PAPER, {"KRW": 5.0})
    assert store.add_paper_account() is False          # a re-run changes nothing
    assert qdb.main(["add-paper-account"]) == 0
    assert store.cash(PAPER)["KRW"] == 5.0
    assert store.is_paper(PAPER) and not store.is_paper("robo")


def test_old_book_gains_account_id_and_backfills_to_the_live_row(tmp_path, monkeypatch):
    """A quant.db from before the paper account: the three extension tables
    grow `account_id`, and their rows are stamped with the account that was
    there — read from the table, not a constant."""
    old = re.sub(r"\n\s*account_id\s+TEXT,", "", qdb.EXTENSION)
    assert old != qdb.EXTENSION
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    from src.ledger import apply as apply_ledger
    apply_ledger(conn)
    conn.executescript(old)
    conn.execute("INSERT INTO accounts (account_id, name, kind, created_at)"
                 " VALUES ('robo_live', 'r', 'auto', 0)")
    conn.execute("INSERT INTO cycles (slot, phase, status, started_at)"
                 " VALUES ('morning', 'done', 'ok', 0)")
    conn.execute("INSERT INTO journal (cycle_id, kind, payload, created_at)"
                 " VALUES (1, 'brief', '{}', 0)")
    conn.commit(); conn.close()

    monkeypatch.setattr(qdb, "QUANT_DB_PATH", path)
    monkeypatch.setattr(qdb, "_initialized", False)
    assert store.last_cycle("robo_live")["cycle_id"] == 1
    assert store.journal_recent("robo_live")[0]["kind"] == "brief"
    assert store.last_cycle("robo") is None


# ── (b) the books do not mix ─────────────────────────────────────────────

def test_plans_holdings_cash_journal_stay_in_their_book():
    a, b = _plan("robo"), _plan(PAPER, sid="KR:000660")
    store.upsert_position("robo", "US:AAPL", qty=1, avg_cost=1, currency="USD")
    store.journal("robo", None, "x", {}, rationale="real")
    store.journal(PAPER, None, "x", {}, rationale="paper")
    store.watch_add(PAPER, "US:NVDA", "watch")

    assert [p["plan_id"] for p in plans.active("robo")] == [a]
    assert [p["plan_id"] for p in plans.active(PAPER)] == [b]
    assert store.positions(PAPER) == []
    assert store.cash("robo") == {}
    assert [e["rationale"] for e in store.journal_recent(PAPER)] == ["paper"]
    assert store.tracked_symbols("robo") == ["US:AAPL"]
    assert store.tracked_symbols(PAPER) == ["US:NVDA"]
    assert "#" + str(b) not in plans.for_context("robo")


# ── (c) a met entry fills at the plan price, no broker ───────────────────

def test_paper_entry_fills_in_the_ledger(monkeypatch):
    pid = _plan(PAPER, qty=3, price=70000)
    monkeypatch.setattr(keeper, "_quote", lambda sid: 69500.0)   # crossed below 70,000
    out = keeper.run_account(PAPER)
    assert out == {"checked": 1, "fired": 1, "watched": 0}

    p = plans.get(pid)
    assert p["status"] == "entered" and p["conditional_order_id"] is None
    assert "모의 체결" in p["note"]
    pos = store.position(PAPER, "KR:005930")
    assert pos["qty"] == 3 and pos["avg_cost"] == 70000     # plan price, not 69,500
    assert pos["entry_plan_id"] == pid
    trade = store.trades_recent(PAPER)[0]
    assert (trade["side"], trade["qty"], trade["price"], trade["source"]) == (
        "buy", 3, 70000, "paper")
    assert trade["fee"] == pytest.approx(3 * 70000 * 0.0015)
    assert store.cash(PAPER)["KRW"] == pytest.approx(1_000_000 - 210000 * 1.0015)
    order = store.orders_recent(PAPER)[0]
    assert order["status"] == "filled" and order["filled_price"] == 70000
    assert store.last_fill_at(PAPER)
    # the real book saw none of it
    assert store.positions("robo") == [] and store.trades_recent("robo") == []


def test_paper_immediate_order_from_the_cycle_fills_too():
    res = execute.execute_orders(PAPER, None, [{
        "symbol_id": "US:AAPL", "name": "Apple", "side": "buy",
        "order_type": "limit", "qty": 2, "price": 200.0, "rationale": "r"}],
        dry_run=False)
    assert res[0]["status"] == "filled"
    assert execute.went_through(res) == res
    assert store.cash(PAPER)["USD"] == pytest.approx(1000 - 400 * 1.0015)
    assert store.position_qty(PAPER, "US:AAPL") == 2


# ── (d) cash short → rejected ────────────────────────────────────────────

def test_paper_buy_beyond_cash_is_rejected(monkeypatch):
    pid = _plan(PAPER, qty=100, price=70000)          # 7,000,000 > 1,000,000
    monkeypatch.setattr(keeper, "_quote", lambda sid: 69000.0)
    out = keeper.run_account(PAPER)
    assert out["fired"] == 1
    p = plans.get(pid)
    assert p["status"] == "armed"
    assert "rejected: paper: insufficient cash" in p["note"]
    assert store.positions(PAPER) == [] and store.trades_recent(PAPER) == []
    assert store.cash(PAPER)["KRW"] == 1_000_000
    assert store.orders_recent(PAPER)[0]["status"] == "rejected"


def test_paper_sell_beyond_holding_is_rejected():
    res = execute.execute_orders(PAPER, None, [{
        "symbol_id": "KR:005930", "side": "sell", "order_type": "limit",
        "qty": 1, "price": 70000}], dry_run=False)
    assert res[0]["status"] == "rejected" and "not held" in res[0]["broker"]["error"]


# ── (e) stop → stopped, cash back ────────────────────────────────────────

def test_paper_stop_returns_the_cash_at_the_stop_price(monkeypatch):
    pid = _plan(PAPER, qty=3, price=70000)
    monkeypatch.setattr(keeper, "_quote", lambda sid: 69000.0)
    keeper.run_account(PAPER)                          # entered @ 70,000
    after_buy = store.cash(PAPER)["KRW"]

    monkeypatch.setattr(keeper, "_quote", lambda sid: 55000.0)  # gapped through the stop
    out = keeper.run_account(PAPER)
    assert out["fired"] == 1
    assert plans.get(pid)["status"] == "stopped"
    assert store.position(PAPER, "KR:005930") is None
    sell = store.trades_recent(PAPER)[0]
    assert sell["side"] == "sell" and sell["price"] == 60000   # stop price, not 55,000
    assert store.cash(PAPER)["KRW"] == pytest.approx(after_buy + 180000 * (1 - 0.0015))


def test_paper_sell_clamps_to_the_holding(monkeypatch):
    """Same rule as the live _submit: a plan's stored qty may exceed what is
    left after a partial sell — fill what is held, never refuse."""
    pid = _plan(PAPER, qty=3, price=70000)
    monkeypatch.setattr(keeper, "_quote", lambda sid: 69000.0)
    keeper.run_account(PAPER)                          # entered, 3 held
    res = execute.execute_orders(PAPER, None, [{
        "symbol_id": "KR:005930", "side": "sell", "order_type": "limit",
        "qty": 1, "price": 75000}], dry_run=False)
    assert res[0]["status"] == "filled"
    assert store.position_qty(PAPER, "KR:005930") == 2
    monkeypatch.setattr(keeper, "_quote", lambda sid: 55000.0)
    keeper.run_account(PAPER)                          # stop on a 3-share plan
    assert plans.get(pid)["status"] == "stopped"
    assert store.position(PAPER, "KR:005930") is None
    sell = store.trades_recent(PAPER)[0]
    assert sell["qty"] == 2 and sell["price"] == 60000
    assert store.orders_recent(PAPER)[0]["filled_qty"] == 2


def test_paper_fill_without_a_fee_rate_is_an_error_not_a_free_trade(monkeypatch):
    monkeypatch.setattr(broker, "fee_rate", lambda market, default=0.0: default)
    from src.core.notify import discord
    sent = []
    monkeypatch.setattr(discord, "send", lambda text, **k: sent.append(text) or True)
    res = execute.execute_orders(PAPER, None, [{
        "symbol_id": "KR:005930", "side": "buy", "order_type": "limit",
        "qty": 1, "price": 70000}], dry_run=False)
    assert res[0]["status"] == "error" and "fee rate unavailable" in res[0]["error"]
    assert store.orders_recent(PAPER)[0]["status"] == "rejected"
    assert store.trades_recent(PAPER) == []
    assert store.cash(PAPER)["KRW"] == 1_000_000
    assert len(sent) == 1 and "fee rate unavailable" in sent[0]


def test_paper_reconcile_is_a_noop():
    store.upsert_position(PAPER, "KR:005930", qty=1, avg_cost=1, currency="KRW")
    res = execute.reconcile(PAPER, None)                # broker.balance raises
    assert res["errors"] == [] and res["noted"] == [{"paper": "ledger is the truth"}]
    assert store.position_qty(PAPER, "KR:005930") == 1
    assert store.journal_recent(PAPER) == []


def test_cycle_sets_the_account_env_for_each_book(monkeypatch):
    import os
    from src.quant import cycle_job
    seen = []
    monkeypatch.setattr(cycle_job, "_phase0_data", lambda ids: {"ok": True})
    monkeypatch.setattr(cycle_job, "run_account",
                        lambda aid, slot, **k: seen.append(
                            (aid, os.environ.get("QUANT_ACCOUNT"))) or 0)
    assert cycle_job.run_cycle("evening") == 0
    assert seen == [("robo", "robo"), (PAPER, PAPER)]


def test_stop_wins_when_both_exits_are_met(monkeypatch):
    pid = _plan(PAPER, qty=1, price=70000, status="entered")
    store.apply_fill(PAPER, symbol_id="KR:005930", side="buy", qty=1,
                     price=70000, fee=0, currency="KRW")
    monkey = {"KR:005930": 90000.0}                    # above take_profit …
    plans.update_conditions(pid, {"stop": {"kind": "below", "price": 95000},
                                  "take_profit": {"kind": "above", "price": 80000}})
    monkeypatch.setattr(keeper, "_quote", lambda sid: monkey[sid])  # … and below the stop
    keeper.run_account(PAPER)
    assert plans.get(pid)["status"] == "stopped"


def test_keeper_tick_sweeps_both_accounts(monkeypatch):
    store.set_trade_mode("robo", "off")
    pid = _plan(PAPER, qty=1, price=70000)
    monkeypatch.setattr(keeper, "_quote", lambda sid: 69000.0)
    out = keeper.run()
    assert out == {"checked": 1, "fired": 1, "watched": 0, "skipped": ["robo"]}
    assert plans.get(pid)["status"] == "entered"
    store.set_trade_mode(PAPER, "off")
    assert keeper.run() == {"skipped": "mode_off"}


def test_keeper_main_live_flag_exits_nonzero_if_any_account_ignored_it(monkeypatch):
    import sys
    store.set_trade_mode("robo", "off")               # paper stays on
    monkeypatch.setattr(keeper, "_quote", lambda sid: 69000.0)
    monkeypatch.setattr(sys, "argv", ["keeper", "--live"])
    assert keeper.main() == 1
    monkeypatch.setattr(sys, "argv", ["keeper"])
    assert keeper.main() == 0


# ── (f) API: ?account= ───────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(security, "_SECRET_FILE", tmp_path / ".secret")
    monkeypatch.setattr(security, "_secret_cache", None)
    monkeypatch.setattr(qapi, "_price", lambda sid: 10000.0)
    monkeypatch.setattr(qapi, "_name", lambda sid: "테스트종목")
    store.set_pin_hash("robo", security.hash_pin("robo", PIN))
    app = FastAPI()
    app.include_router(qapi.router)
    return TestClient(app, cookies={qapi.COOKIE_NAME: security.make_session_token("robo")})


def test_api_reads_the_paper_book_by_query(client):
    _plan(PAPER, sid="KR:000660")
    r = client.get("/api/quant/state?account=robo_paper")
    assert r.status_code == 200
    body = r.json()
    assert body["account_id"] == PAPER and body["paper"] is True
    assert body["cash"] == {"USD": 1000.0, "KRW": 1_000_000.0}
    assert [a["account_id"] for a in body["accounts"]] == ["robo", PAPER]
    assert client.get("/api/quant/state").json()["account_id"] == "robo"
    assert client.get("/api/quant/plans").json()["active"] == []
    assert len(client.get("/api/quant/plans?account=robo_paper").json()["active"]) == 1
    assert client.get("/api/quant/state?account=nope").status_code == 404


def test_default_account_is_the_primary_row_not_a_constant(client, monkeypatch):
    import sqlite3
    with qdb.connect() as c:
        c.execute("UPDATE accounts SET sort_order = -1 WHERE account_id = ?", (PAPER,))
        c.commit()
    assert store.primary_account_id() == PAPER
    monkeypatch.delenv("QUANT_ACCOUNT", raising=False)
    from src.quant import tools
    assert tools._acct() == PAPER
    monkeypatch.setenv("QUANT_ACCOUNT", "robo")
    assert tools._acct() == "robo"


def test_tools_answer_from_the_ledger_for_the_paper_book(monkeypatch):
    from src.quant import tools
    monkeypatch.setenv("QUANT_ACCOUNT", PAPER)
    store.apply_fill(PAPER, symbol_id="KR:005930", side="buy", qty=1,
                     price=70000, fee=0, currency="KRW", source="paper")
    assert tools._cmd_balance()["cash"]["KRW"] == 930000
    assert tools._cmd_sellable("KR:005930") == {"paper": True, "qty": 1.0}
    assert tools._cmd_buyable("KR:005930", "70000")["max_qty"] == 13
    assert tools._cmd_pending() == []
    assert tools._cmd_conditionals()["conditionals"] == []
    assert tools._cmd_fills("20260101", "20261231")[0]["source"] == "paper"
    for fn, args in ((tools._cmd_order_detail, ("1",)), (tools._cmd_accounts, ()),
                     (tools._cmd_commissions, ())):
        assert "paper" in fn(*args)["error"]
    monkeypatch.setenv("QUANT_ALLOW_WRITE", "1")
    for fn, args in ((tools._cmd_order, ("KR:005930", "buy", "1", "1")),
                     (tools._cmd_cancel, ("1",)),
                     (tools._cmd_conditional_add, ("KR:005930", "buy", "1", "1")),
                     (tools._cmd_conditional_cancel, ("1",))):
        assert "paper" in fn(*args)["error"]


def test_api_paper_book_refuses_broker_only_writes(client):
    for path, body in (("/mode", {"mode": "off", "pin": PIN}),
                       ("/sync", {"pin": PIN}),
                       ("/orders/cancel_all", {"pin": PIN})):
        r = client.post(f"/api/quant{path}?account=robo_paper", json=body)
        assert r.status_code == 400, path
    assert store.trade_mode(PAPER) == "live"
    # the same cookie, no PIN: still 403 before the 400
    assert client.post("/api/quant/mode?account=robo_paper",
                       json={"mode": "off"}).status_code == 403


def test_api_plan_writes_land_in_the_named_book(client):
    r = client.post("/api/quant/plans?account=robo_paper", json={
        "symbol_id": "KR:005930", "side": "buy", "qty": 1, "price": 70000,
        "conditions": {"entry": {"kind": "below", "price": 68000}}, "pin": PIN})
    assert r.status_code == 200, r.text
    assert r.json()["conditional"]["skipped"].startswith("paper")
    pid = r.json()["plan"]["plan_id"]
    assert plans.active("robo") == [] and plans.get(pid)["account_id"] == PAPER
    # a plan is only reachable through its own book
    assert client.patch(f"/api/quant/plans/{pid}",
                        json={"status": "abandoned"}).status_code == 404
    assert client.patch(f"/api/quant/plans/{pid}?account=robo_paper",
                        json={"status": "abandoned"}).status_code == 200
    r = client.post("/api/quant/watch?account=robo_paper",
                    json={"symbol_id": "US:NVDA"})
    assert r.status_code == 200
    assert store.watch_list("robo") == [] and len(store.watch_list(PAPER)) == 1
