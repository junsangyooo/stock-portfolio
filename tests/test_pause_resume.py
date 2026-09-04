"""Pause / resume per quant account.

Paused = no new entries, exits still guarded. Resume = five deterministic
rules over every armed plan, in order: expired → analysis stale → thesis
broken → target passed → kept (and re-armed). A missing quote keeps the plan
and says so.
"""
import sys
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.quant.broker as broker
from src.api import quant as qapi
from src.core import security
from src.core.kst import today
from src.core.notify import discord
from src.quant import cycle_job, db as qdb, execute, keeper, plans, store
from src.research import analyze

PAPER = qdb.PAPER_ACCOUNT_ID
PIN = "030220"


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(qdb, "QUANT_DB_PATH", tmp_path / "quant.db")
    monkeypatch.setattr(qdb, "_initialized", False)
    store.create_account("robo", "로보", broker="toss")
    store.set_trade_mode("robo", "live")
    store.add_paper_account()
    monkeypatch.setattr(keeper, "market_open", lambda m, now: True)
    monkeypatch.setattr(broker, "fee_rate", lambda market, default=0.0: 0.0015)

    def _no_broker(*a, **k):
        raise AssertionError(f"reached the broker: {a}")
    for fn in ("place_order", "place_conditional", "place_oco", "buyable",
               "balance", "cancel_conditional", "conditional", "quote",
               "pending", "conditionals"):
        monkeypatch.setattr(broker, fn, _no_broker)
    monkeypatch.setattr(analyze, "latest_date", lambda sid: today())


@pytest.fixture
def sent(monkeypatch):
    msgs = []
    monkeypatch.setattr(discord, "send", lambda text, *a, **k: msgs.append(text) or True)
    return msgs


def _plan(account, sid="KR:005930", **over):
    kw = dict(symbol_id=sid, name="삼성전자", side="buy", qty=3, price=70000,
              conditions={"entry": {"kind": "below", "price": 70000},
                          "stop": {"kind": "below", "price": 60000},
                          "take_profit": {"kind": "above", "price": 80000}})
    kw.update(over)
    return plans.create(account, **kw)


# ── state ────────────────────────────────────────────────────────────────

def test_pause_and_resume_flip_the_column(sent):
    assert not store.is_paused(PAPER)
    execute.pause_account(PAPER)
    assert store.is_paused(PAPER) and store.account(PAPER)["paused_at"]
    assert store.journal_recent(PAPER)[0]["kind"] == "pause"
    execute.resume_account(PAPER)
    assert not store.is_paused(PAPER) and store.account(PAPER)["paused_at"] is None
    assert store.journal_recent(PAPER)[0]["kind"] == "resume"
    assert len(sent) == 2


# ── keeper while paused ──────────────────────────────────────────────────

def test_paused_keeper_skips_entries(monkeypatch, sent):
    pid = _plan(PAPER)
    execute.pause_account(PAPER)
    assert "[pause] entry watch cancelled" in plans.get(pid)["note"]
    monkeypatch.setattr(keeper, "_quote", lambda sid: 65000.0)   # entry met
    out = keeper.run_account(PAPER)
    assert out["fired"] == 0
    assert plans.get(pid)["status"] == "armed"
    assert store.trades_recent(PAPER) == []


def test_paused_keeper_still_fires_the_stop(monkeypatch, sent):
    pid = _plan(PAPER)
    monkeypatch.setattr(keeper, "_quote", lambda sid: 69000.0)
    keeper.run_account(PAPER)                                    # entered
    execute.pause_account(PAPER)
    monkeypatch.setattr(keeper, "_quote", lambda sid: 55000.0)
    out = keeper.run_account(PAPER)
    assert out["fired"] == 1
    assert plans.get(pid)["status"] == "stopped"
    assert store.position(PAPER, "KR:005930") is None
    assert len(store.snapshots(PAPER)) == 1                      # snapshot still runs


def test_paused_keeper_still_expires(monkeypatch, sent):
    pid = _plan(PAPER, expires_at=int(time.time()) - 1)
    execute.pause_account(PAPER)
    monkeypatch.setattr(keeper, "_quote", lambda sid: 65000.0)
    keeper.run_account(PAPER)
    assert plans.get(pid)["status"] == "expired"


# ── pause cancels only the entry watch ───────────────────────────────────

def test_pause_cancels_entry_watch_but_keeps_exit_oco(monkeypatch, sent):
    armed = _plan("robo")
    entered = _plan("robo", sid="KR:000660", status="entered")
    plans.set_conditional(armed, "C-entry")
    plans.set_conditional(entered, "C-oco")
    cancelled = []
    monkeypatch.setattr(broker, "cancel_conditional",
                        lambda cid: cancelled.append(cid) or {"ok": True})
    res = execute.pause_account("robo")
    assert cancelled == ["C-entry"]
    assert plans.get(armed)["conditional_order_id"] is None
    assert plans.get(armed)["status"] == "armed"
    assert "[pause] entry watch cancelled" in plans.get(armed)["note"]
    assert plans.get(entered)["conditional_order_id"] == "C-oco"
    assert res["entry_watch_cancelled"] == [armed]
    assert len(sent) == 1 and "robo" in sent[0]


# ── resume rules ─────────────────────────────────────────────────────────

@pytest.fixture
def rearm(monkeypatch):
    """Live broker registration stubbed: records which plans got re-armed."""
    calls = []
    monkeypatch.setattr(broker, "place_conditional",
                        lambda sid, side, qty, **k: calls.append(sid) or
                        {"ok": True, "conditional_order_id": "C-new"})
    monkeypatch.setattr(broker, "place_oco",
                        lambda sid, qty, **k: calls.append("oco:" + sid) or
                        {"ok": True, "conditional_order_id": "C-oco-new"})
    return calls


def test_resume_rule1_expired(monkeypatch, sent, rearm):
    pid = _plan("robo", expires_at=int(time.time()) - 1)
    monkeypatch.setattr(broker, "quote", lambda sid: 65000.0)
    rep = execute.resume_account("robo")
    assert plans.get(pid)["status"] == "expired"
    assert rep["expired"] == [pid] and rearm == []


def test_resume_rule2_analysis_stale(monkeypatch, sent, rearm):
    pid = _plan("robo")
    none = _plan("robo", sid="KR:000660")
    from datetime import date, timedelta
    old = (date.today() - timedelta(days=10)).isoformat()
    monkeypatch.setattr(analyze, "latest_date",
                        lambda sid: old if sid == "KR:005930" else None)
    monkeypatch.setattr(broker, "quote", lambda sid: 65000.0)
    rep = execute.resume_account("robo")
    for p in (pid, none):
        assert plans.get(p)["status"] == "abandoned"
        assert "[resume] analysis stale" in plans.get(p)["note"]
    assert sorted(rep["stale"]) == sorted([pid, none]) and rearm == []


def test_analysis_staleness_counts_trading_days_not_calendar_days(monkeypatch):
    """Friday's analysis on Monday is one trading day old — still fresh."""
    from datetime import date, timedelta
    monkeypatch.setattr(broker, "is_trading_day",
                        lambda market, ymd=None: date(int(ymd[:4]), int(ymd[4:6]),
                                                      int(ymd[6:])).weekday() < 5)
    monday = date(2026, 9, 7)
    fri = (monday - timedelta(days=3)).isoformat()
    monkeypatch.setattr(analyze, "latest_date", lambda sid: fri)
    assert not execute._analysis_stale("KR:005930", today=monday)
    assert execute._analysis_stale("KR:005930", today=monday + timedelta(days=2))


def test_resume_rule3_thesis_broken(monkeypatch, sent, rearm):
    pid = _plan("robo")
    monkeypatch.setattr(broker, "quote", lambda sid: 59000.0)     # under the stop
    rep = execute.resume_account("robo")
    assert plans.get(pid)["status"] == "abandoned"
    assert "[resume] thesis broken" in plans.get(pid)["note"]
    assert rep["thesis_broken"] == [pid] and rearm == []


def test_resume_rule4_target_passed(monkeypatch, sent, rearm):
    pid = _plan("robo")
    monkeypatch.setattr(broker, "quote", lambda sid: 81000.0)     # over take_profit
    rep = execute.resume_account("robo")
    assert plans.get(pid)["status"] == "abandoned"
    assert "[resume] target passed" in plans.get(pid)["note"]
    assert rep["target_passed"] == [pid]


def test_resume_rule5_kept_and_rearmed(monkeypatch, sent, rearm):
    pid = _plan("robo")
    held = _plan("robo", sid="KR:000660", status="entered")       # exits unguarded
    monkeypatch.setattr(broker, "quote", lambda sid: 72000.0)
    rep = execute.resume_account("robo")
    p = plans.get(pid)
    assert p["status"] == "armed" and "[resume] kept" in p["note"]
    assert p["conditional_order_id"] == "C-new"
    assert plans.get(held)["conditional_order_id"] == "C-oco-new"
    assert rep["kept"] == [pid]
    assert rearm == ["KR:005930", "oco:KR:000660"]
    assert "유지 1" in sent[-1] and "폐기 0" in sent[-1]
    assert store.last_resume("robo") == rep


def test_resume_without_a_quote_keeps_and_flags(monkeypatch, sent, rearm):
    pid = _plan("robo")
    monkeypatch.setattr(broker, "quote", lambda sid: None)
    rep = execute.resume_account("robo")
    p = plans.get(pid)
    assert p["status"] == "armed" and "[resume] quote unavailable, kept" in p["note"]
    assert rep["quote_unavailable"] == [pid] and rep["kept"] == []
    assert rearm == ["KR:005930"]


def test_resume_paper_registers_nothing(monkeypatch, sent):
    pid = _plan(PAPER)
    monkeypatch.setattr(broker, "quote", lambda sid: 72000.0)
    execute.pause_account(PAPER)
    rep = execute.resume_account(PAPER)
    assert rep["kept"] == [pid] and plans.get(pid)["conditional_order_id"] is None


# ── cycle skips a paused account ─────────────────────────────────────────

def test_cycle_skips_a_paused_account(monkeypatch, sent):
    monkeypatch.setattr(cycle_job, "_phase0_data", lambda: {"ok": True})
    monkeypatch.setattr(cycle_job.session, "run_main",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")))
    store.set_trade_mode("robo", "off")
    execute.pause_account(PAPER)
    sent.clear()
    assert cycle_job.run_cycle("evening") == 0
    assert store.last_cycle(PAPER) is None
    assert len(sent) == 1 and "paused" in sent[0]


# ── API ──────────────────────────────────────────────────────────────────

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


def test_api_pause_resume_round_trip(client, monkeypatch, sent):
    monkeypatch.setattr(broker, "quote", lambda sid: 72000.0)
    q = "?account=robo_paper"
    assert client.post("/api/quant/pause" + q, json={}).status_code == 403   # PIN step-up
    st = client.get("/api/quant/state" + q).json()
    assert st["paused_at"] is None and st["last_resume"] is None

    assert client.post("/api/quant/pause" + q, json={"pin": PIN}).status_code == 200
    assert client.post("/api/quant/pause" + q, json={"pin": PIN}).status_code == 400
    assert client.get("/api/quant/state" + q).json()["paused_at"]
    assert client.get("/api/quant/state").json()["paused_at"] is None       # robo untouched

    r = client.post("/api/quant/resume" + q, json={"pin": PIN})
    assert r.status_code == 200 and "kept" in r.json()["report"]
    assert client.post("/api/quant/resume" + q, json={"pin": PIN}).status_code == 400
    st = client.get("/api/quant/state" + q).json()
    assert st["paused_at"] is None and st["last_resume"] == r.json()["report"]


# ── pause lands while a cycle is running ─────────────────────────────────

def test_pause_between_plan_creation_and_arming_skips_the_broker(monkeypatch, sent, rearm):
    """A cycle runs for hours. A pause that lands after the plan is written
    but before its entry watch is registered must still mean "no new
    entries": the plan is kept (unarmed, NULL watch id) and resume re-judges
    it with the rest."""
    monkeypatch.setattr(cycle_job, "_phase0_data", lambda: {"ok": True})
    monkeypatch.setattr(execute, "reconcile", lambda aid, cid: {})
    monkeypatch.setattr(execute, "execute_orders", lambda *a, **k: [])
    monkeypatch.setattr(execute, "cancel_conditionals", lambda *a, **k: None)
    monkeypatch.setattr(cycle_job.session, "run_main", lambda aid, cid, focus: ("s" * 12, ""))
    monkeypatch.setattr(cycle_job.session, "run_explorers",
                        lambda aid, cid, sid, on_holdings_done: ({}, {}))
    monkeypatch.setattr(cycle_job.session, "run_allocation", lambda aid, cid, **k: {
        "orders": [], "rejected": [], "plans": [{
            "symbol_id": "KR:005930", "name": "삼성전자", "side": "buy", "qty": 3,
            "price": 70000, "rationale": "근거", "source": "discover",
            "conditions": {"entry": {"kind": "below", "price": 70000},
                           "stop": {"kind": "below", "price": 60000},
                           "take_profit": {"kind": "above", "price": 80000}}}]})
    real_create = plans.create

    def create_then_pause(*a, **k):
        pid = real_create(*a, **k)
        store.pause("robo")                     # the operator, mid-cycle
        return pid
    monkeypatch.setattr(cycle_job.plan_ledger, "create", create_then_pause)

    assert cycle_job.run_account("robo", "morning") == 0
    cyc = store.last_cycle("robo")
    assert cyc["status"] == "ok"                # no broker call blew the cycle up
    assert rearm == []
    (p,) = plans.active("robo")
    assert p["status"] == "armed" and p["conditional_order_id"] is None
    assert p["cycle_id"] == cyc["cycle_id"]
    skips = [e for e in store.journal_recent("robo", cycle_id=cyc["cycle_id"])
             if e["kind"] == "paused_skip"]
    assert [e["payload"]["plan_id"] for e in skips] == [p["plan_id"]]

    # resume: rule 5 keeps it and registers the watch it never got
    monkeypatch.setattr(broker, "quote", lambda sid: 72000.0)
    rep = execute.resume_account("robo")
    assert rep["kept"] == [p["plan_id"]] and rearm == ["KR:005930"]
    assert plans.get(p["plan_id"])["conditional_order_id"] == "C-new"
    # ...and rule 3 abandons it when the stop was hit meanwhile
    store.pause("robo")
    monkeypatch.setattr(broker, "quote", lambda sid: 59000.0)
    plans.set_conditional(p["plan_id"], None)
    rep = execute.resume_account("robo")
    assert rep["thesis_broken"] == [p["plan_id"]]
    assert plans.get(p["plan_id"])["status"] == "abandoned"


def test_paused_execute_orders_skips_buys_but_sends_sells(monkeypatch, sent):
    """Immediate orders from an allocation: while paused a buy is a new entry
    and never reaches the broker; a sell is an exit and still goes out."""
    store.upsert_position("robo", "KR:000660", qty=3, avg_cost=100000,
                          currency="KRW")
    store.pause("robo")
    placed = []
    monkeypatch.setattr(broker, "place_order",
                        lambda sid, side, qty, price, order_type:
                        placed.append((sid, side)) or {"ok": True, "order_no": "O1"})
    monkeypatch.setattr(broker, "buyable",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("sized a buy")))
    cid = store.cycle_start("robo", "morning", "KR", dry_run=False)
    res = execute.execute_orders("robo", cid, [
        {"symbol_id": "KR:005930", "side": "buy", "qty": 1, "price": 70000,
         "rationale": "신규"},
        {"symbol_id": "KR:000660", "side": "sell", "qty": 3, "price": 120000,
         "rationale": "청산"},
    ], dry_run=False)
    assert placed == [("KR:000660", "sell")]
    assert [r["status"] for r in res] == ["paused_skip", "submitted"]
    assert execute.went_through(res) == [res[1]]
    assert [o["side"] for o in store.orders_for_cycle(cid)] == ["sell"]   # no idem row for the buy
    skips = [e for e in store.journal_recent("robo", cycle_id=cid) if e["kind"] == "paused_skip"]
    assert len(skips) == 1 and skips[0]["payload"]["symbol_id"] == "KR:005930"
