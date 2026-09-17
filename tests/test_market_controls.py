"""Per-market controls (KR · US) on the quant accounts, and the exits they
must never touch.

A market is `active` or `disabled`. Disabled means no cycle, no new buy entry
and no analysis warm for that market — and nothing else: a sell plan and the
stop of an entered position are exits, watched whatever the market's state.
Turning a market back on re-judges its buy plans, re-analyzing a symbol whose
analysis went stale before judging it.

Stops go to the broker as a single MARKET conditional (Toss OCO is LIMIT
only, and a limit stop does not fill on a gap); take-profits are the keeper's.
"""
import time
from datetime import date, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.quant.broker as broker
from src.api import analysis_jobs, quant as qapi
from src.core import security
from src.core.kst import today
from src.core.notify import discord
from src.quant import cycle_job, db as qdb, execute, jobs, keeper, plans, session, store
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
    for fn in ("place_order", "place_conditional", "buyable", "balance",
               "cancel_conditional", "conditional", "quote", "pending",
               "conditionals"):
        monkeypatch.setattr(broker, fn, _no_broker)
    monkeypatch.setattr(analyze, "latest_date", lambda sid: today())
    monkeypatch.setattr(execute, "_reanalyze",
                        lambda symbols: (_ for _ in ()).throw(AssertionError("reanalyzed")))


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


def _exit(account, sid="KR:005930", kind="below", price=60000, **over):
    """A standalone sell plan — how the cycle writes a holding's stop
    (entry below) or take-profit (entry above)."""
    return _plan(account, sid, side="sell", price=price,
                 conditions={"entry": {"kind": kind, "price": price}}, **over)


def _hold(account, sid="KR:005930", qty=3, price=70000):
    store.apply_fill(account, symbol_id=sid, side="buy", qty=qty, price=price,
                     fee=0, currency="USD" if sid.startswith("US:") else "KRW")


# ── storage ──────────────────────────────────────────────────────────────

def test_new_accounts_start_with_both_markets_active():
    for a in ("robo", PAPER):
        assert store.active_markets(a) == {"KR", "US"}
        assert set(store.market_states(a)) == {"KR", "US"}


def test_missing_row_reads_disabled():
    with qdb.connect() as c:
        c.execute("DELETE FROM markets WHERE account_id = 'robo' AND market = 'US'")
        c.commit()
    assert store.market_state("robo", "US") == "disabled"
    assert store.active_markets("robo") == {"KR"}


def test_unknown_market_or_state_is_refused():
    with pytest.raises(ValueError):
        store.set_market_state("robo", "JP", "active")
    with pytest.raises(ValueError):
        store.set_market_state("robo", "KR", "paused")


# ── turning a market off ─────────────────────────────────────────────────

def test_market_off_withdraws_only_buy_entry_watches_in_that_market(monkeypatch, sent):
    buy_kr = _plan("robo")
    sell_kr = _exit("robo", sid="KR:000660")
    held_kr = _plan("robo", sid="KR:035420", status="entered")
    buy_us = _plan("robo", sid="US:AAPL", price=150)
    for pid, cid in ((buy_kr, "C-buy"), (sell_kr, "C-stop"), (held_kr, "C-held"),
                     (buy_us, "C-us")):
        plans.set_conditional(pid, cid)
    cancelled = []
    monkeypatch.setattr(broker, "cancel_conditional",
                        lambda cid: cancelled.append(cid) or {"ok": True})

    res = execute.market_off("robo", "KR")

    assert cancelled == ["C-buy"]
    assert res["entry_watch_cancelled"] == [buy_kr] and res["errors"] == []
    assert plans.get(buy_kr)["conditional_order_id"] is None
    assert plans.get(buy_kr)["status"] == "armed"
    for pid, cid in ((sell_kr, "C-stop"), (held_kr, "C-held"), (buy_us, "C-us")):
        assert plans.get(pid)["conditional_order_id"] == cid
    assert store.market_state("robo", "KR") == "disabled"
    assert store.market_state("robo", "US") == "active"
    assert store.journal_recent("robo")[0]["kind"] == "market_off"
    assert len(sent) == 1 and "KR" in sent[0]


def test_market_off_cancel_failure_keeps_the_watch_id(monkeypatch, sent):
    pid = _plan("robo")
    plans.set_conditional(pid, "C-buy")
    monkeypatch.setattr(broker, "cancel_conditional", lambda cid: {"error": "429"})
    res = execute.market_off("robo", "KR")
    assert plans.get(pid)["conditional_order_id"] == "C-buy"
    assert res["errors"][0]["plan_id"] == pid
    assert store.market_state("robo", "KR") == "disabled"


def test_cancel_conditionals_keeps_the_id_when_the_broker_refuses(monkeypatch):
    pid = _plan("robo", status="entered")
    plans.set_conditional(pid, "C-stop")
    monkeypatch.setattr(broker, "cancel_conditional", lambda cid: {"error": "timeout"})
    out = execute.cancel_conditionals("robo", None, ["C-stop"], dry_run=False)
    assert "error" in out[0]
    assert plans.get(pid)["conditional_order_id"] == "C-stop"
    monkeypatch.setattr(broker, "cancel_conditional", lambda cid: {"ok": True})
    execute.cancel_conditionals("robo", None, ["C-stop"], dry_run=False)
    assert plans.get(pid)["conditional_order_id"] is None


# ── keeper: entries respect the market, exits do not ─────────────────────

def test_keeper_enters_only_in_an_active_market(monkeypatch, sent):
    kr = _plan(PAPER)
    us = _plan(PAPER, sid="US:AAPL", price=150,
               conditions={"entry": {"kind": "below", "price": 150}})
    store.set_market_state(PAPER, "KR", "disabled")
    monkeypatch.setattr(keeper, "_quote", lambda sid: 140.0 if sid.startswith("US") else 65000.0)
    out = keeper.run_account(PAPER)
    assert out["fired"] == 1
    assert plans.get(kr)["status"] == "armed"
    assert plans.get(us)["status"] == "entered"


def test_keeper_fires_a_sell_stop_in_a_disabled_market_at_the_observed_price(monkeypatch, sent):
    _hold(PAPER, qty=3, price=70000)
    pid = _exit(PAPER, kind="below", price=60000)
    store.set_market_state(PAPER, "KR", "disabled")
    monkeypatch.setattr(keeper, "_quote", lambda sid: 55000.0)   # gapped through
    out = keeper.run_account(PAPER)
    assert out["fired"] == 1
    assert plans.get(pid)["status"] == "stopped"
    assert store.position(PAPER, "KR:005930") is None
    assert store.trades_recent(PAPER)[0]["price"] == 55000       # a market stop, not 60,000
    assert plans.get(pid) not in plans.active(PAPER)


def test_keeper_sell_take_profit_plan_fills_at_the_plan_price_and_closes(monkeypatch, sent):
    _hold(PAPER, qty=3, price=70000)
    pid = _exit(PAPER, kind="above", price=80000)
    store.set_market_state(PAPER, "KR", "disabled")
    monkeypatch.setattr(keeper, "_quote", lambda sid: 83000.0)
    keeper.run_account(PAPER)
    assert plans.get(pid)["status"] == "done"
    assert store.trades_recent(PAPER)[0]["price"] == 80000


def test_keeper_fires_an_entered_stop_and_expiry_in_a_disabled_market(monkeypatch, sent):
    pid = _plan(PAPER)
    monkeypatch.setattr(keeper, "_quote", lambda sid: 69000.0)
    keeper.run_account(PAPER)                                    # entered @ 70,000
    old = _plan(PAPER, sid="KR:000660", expires_at=int(time.time()) - 1)
    store.set_market_state(PAPER, "KR", "disabled")
    monkeypatch.setattr(keeper, "_quote", lambda sid: 58000.0)
    out = keeper.run_account(PAPER)
    assert out["fired"] == 1
    assert plans.get(pid)["status"] == "stopped"
    assert store.trades_recent(PAPER)[0]["price"] == 58000
    assert plans.get(old)["status"] == "expired"
    assert len(store.snapshots(PAPER)) == 1


def test_paper_entry_and_take_profit_fill_at_the_plan_price(monkeypatch, sent):
    pid = _plan(PAPER)
    monkeypatch.setattr(keeper, "_quote", lambda sid: 66000.0)
    keeper.run_account(PAPER)
    assert store.trades_recent(PAPER)[0]["price"] == 70000
    monkeypatch.setattr(keeper, "_quote", lambda sid: 85000.0)
    keeper.run_account(PAPER)
    assert plans.get(pid)["status"] == "done"
    assert store.trades_recent(PAPER)[0]["price"] == 80000


# ── the live exit split: broker stop (market), keeper take-profit ────────

def test_an_entered_plan_registers_its_stop_alone_as_a_market_conditional(monkeypatch):
    pid = _plan("robo", status="entered")
    calls = []
    monkeypatch.setattr(broker, "place_conditional",
                        lambda sid, side, qty, **k: calls.append((sid, side, qty, k)) or
                        {"ok": True, "conditional_order_id": "C-stop"})
    execute.arm_conditional("robo", None, pid, dry_run=False)
    (sid, side, qty, k), = calls
    assert (sid, side, qty) == ("KR:005930", "sell", 3)
    assert k["trigger"] == 60000 and k.get("price") is None      # None = MARKET
    assert plans.get(pid)["conditional_order_id"] == "C-stop"
    assert not hasattr(broker, "place_oco")


def test_a_sell_stop_plan_is_a_market_conditional_and_a_take_profit_is_the_keepers(monkeypatch):
    stop = _exit("robo", kind="below", price=60000)
    tp = _exit("robo", sid="KR:000660", kind="above", price=90000)
    calls = []
    monkeypatch.setattr(broker, "place_conditional",
                        lambda sid, side, qty, **k: calls.append((sid, k.get("price"))) or
                        {"ok": True, "conditional_order_id": "C1"})
    execute.arm_conditional("robo", None, stop, dry_run=False)
    res = execute.arm_conditional("robo", None, tp, dry_run=False)
    assert calls == [("KR:005930", None)]
    assert "skipped" in res and plans.get(tp)["conditional_order_id"] is None


def test_a_buy_entry_stays_a_limit_conditional_at_the_plan_price(monkeypatch):
    pid = _plan("robo")
    calls = []
    monkeypatch.setattr(broker, "place_conditional",
                        lambda sid, side, qty, **k: calls.append((side, k)) or
                        {"ok": True, "conditional_order_id": "C1"})
    execute.arm_conditional("robo", None, pid, dry_run=False)
    assert calls == [("buy", {"trigger": 70000.0, "price": 70000.0, "expire_date": ""})]


def test_arm_refuses_a_buy_entry_in_a_disabled_market_but_arms_its_exits(monkeypatch):
    store.set_market_state("robo", "KR", "disabled")
    buy = _plan("robo")
    held = _plan("robo", sid="KR:000660", status="entered")
    calls = []
    monkeypatch.setattr(broker, "place_conditional",
                        lambda sid, side, qty, **k: calls.append(sid) or
                        {"ok": True, "conditional_order_id": "C1"})
    assert "skipped" in execute.arm_conditional("robo", None, buy, dry_run=False)
    execute.arm_conditional("robo", None, held, dry_run=False)
    assert calls == ["KR:000660"]


def test_keeper_take_profit_withdraws_the_broker_stop_before_selling(monkeypatch, sent):
    _hold("robo", qty=3)
    pid = _plan("robo", status="entered")
    plans.set_conditional(pid, "C-stop")
    monkeypatch.setattr(broker, "conditional", lambda cid: {"status": "WATCHING"})
    events = []
    monkeypatch.setattr(broker, "cancel_conditional",
                        lambda cid: events.append(("cancel", cid)) or {"ok": True})
    monkeypatch.setattr(broker, "place_order",
                        lambda sid, side, qty, price, order_type:
                        events.append((side, order_type, price)) or {"ok": True, "order_no": "O1"})
    monkeypatch.setattr(keeper, "_quote", lambda sid: 81000.0)
    keeper.run_account("robo")
    assert events == [("cancel", "C-stop"), ("sell", "limit", 81000.0)]
    p = plans.get(pid)
    assert p["status"] == "done" and p["conditional_order_id"] is None


def test_keeper_take_profit_waits_when_the_stop_cannot_be_withdrawn(monkeypatch, sent):
    _hold("robo", qty=3)
    pid = _plan("robo", status="entered")
    plans.set_conditional(pid, "C-stop")
    monkeypatch.setattr(broker, "conditional", lambda cid: {"status": "WATCHING"})
    monkeypatch.setattr(broker, "cancel_conditional", lambda cid: {"error": "timeout"})
    placed = []
    monkeypatch.setattr(broker, "place_order", lambda *a, **k: placed.append(a) or {"ok": True})
    monkeypatch.setattr(keeper, "_quote", lambda sid: 81000.0)
    out = keeper.run_account("robo")
    assert out["fired"] == 0 and placed == []
    p = plans.get(pid)
    assert p["status"] == "entered" and p["conditional_order_id"] == "C-stop"


def test_keeper_leaves_a_broker_owned_stop_alone_below_the_target(monkeypatch, sent):
    pid = _plan("robo", status="entered")
    plans.set_conditional(pid, "C-stop")
    monkeypatch.setattr(broker, "conditional", lambda cid: {"status": "WATCHING"})
    monkeypatch.setattr(keeper, "_quote", lambda sid: 55000.0)   # under the stop: the broker's job
    out = keeper.run_account("robo")
    assert out == {"checked": 0, "fired": 0, "watched": 1}
    assert plans.get(pid)["status"] == "entered"


def test_keeper_fallback_stop_is_a_market_order(monkeypatch, sent):
    _hold("robo", qty=3)
    pid = _plan("robo", status="entered")
    placed = []
    monkeypatch.setattr(broker, "place_order",
                        lambda sid, side, qty, price, order_type:
                        placed.append((side, order_type)) or {"ok": True, "order_no": "O1"})
    monkeypatch.setattr(keeper, "_quote", lambda sid: 59000.0)
    keeper.run_account("robo")
    assert placed == [("sell", "market")]
    assert plans.get(pid)["status"] == "stopped"


@pytest.mark.parametrize("status, side, conditions, expect, watch", [
    ("entered", "buy", None, "stopped", None),
    ("armed", "sell", {"entry": {"kind": "below", "price": 60000}}, "stopped", None),
    # a broker-filled entry hands its stop to the broker at once, not to polling
    ("armed", "buy", None, "entered", "C-stop"),
])
def test_a_completed_broker_conditional_advances_the_plan(monkeypatch, sent, status,
                                                         side, conditions, expect, watch):
    over = {"status": status, "side": side}
    if conditions:
        over["conditions"] = conditions
    pid = _plan("robo", **over)
    plans.set_conditional(pid, "C1")
    monkeypatch.setattr(broker, "conditional",
                        lambda cid: {"status": "COMPLETED", "first": {"status": "COMPLETED"}})
    monkeypatch.setattr(broker, "place_conditional",
                        lambda sid, side, qty, **k: {"ok": True, "conditional_order_id": "C-stop"})
    keeper.run_account("robo")
    p = plans.get(pid)
    assert p["status"] == expect and p["conditional_order_id"] == watch


# ── one stop and one take-profit per holding ─────────────────────────────

def test_newer_sell_plans_supersede_an_entered_plans_exits(sent):
    """The STNG case: an entered buy still carried its stop and target when
    the cycle wrote standalone sell plans for the same shares."""
    old = _plan(PAPER, sid="US:STNG", price=84, status="entered",
                conditions={"entry": {"kind": "below", "price": 84},
                            "stop": {"kind": "below", "price": 74},
                            "take_profit": {"kind": "above", "price": 96}})
    stop = _exit(PAPER, sid="US:STNG", kind="below", price=79)
    tp = _exit(PAPER, sid="US:STNG", kind="above", price=96)
    execute.dedupe_exits(PAPER, dry_run=False)
    assert plans.get(old)["status"] == "abandoned"
    assert plans.get(stop)["status"] == "armed" and plans.get(tp)["status"] == "armed"


def test_a_newer_stop_replaces_only_the_stop_leg(sent):
    old = _plan(PAPER, status="entered")
    new = _exit(PAPER, kind="below", price=65000)
    execute.dedupe_exits(PAPER, dry_run=False)
    p = plans.get(old)
    assert p["status"] == "entered"
    assert "stop" not in p["conditions"] and p["conditions"]["take_profit"]["price"] == 80000
    assert plans.get(new)["status"] == "armed"


def test_the_newest_of_two_stop_plans_wins_and_the_loser_leaves_the_broker(monkeypatch, sent):
    older = _exit("robo", price=60000)
    newer = _exit("robo", price=62000)
    plans.set_conditional(older, "C-old")
    cancelled = []
    monkeypatch.setattr(broker, "cancel_conditional",
                        lambda cid: cancelled.append(cid) or {"ok": True})
    execute.dedupe_exits("robo", dry_run=False)
    assert plans.get(older)["status"] == "abandoned" and cancelled == ["C-old"]
    assert plans.get(newer)["status"] == "armed"


def test_a_persons_exit_is_never_superseded(sent):
    mine = _exit(PAPER, price=60000, source="user")
    agent = _exit(PAPER, price=62000)
    execute.dedupe_exits(PAPER, dry_run=False)
    assert plans.get(mine)["status"] == "armed"
    assert plans.get(agent)["status"] == "abandoned"


# ── turning a market on: re-judge, re-analyzing what went stale ──────────

@pytest.fixture
def rearm(monkeypatch):
    calls = []
    monkeypatch.setattr(broker, "place_conditional",
                        lambda sid, side, qty, **k: calls.append((sid, side)) or
                        {"ok": True, "conditional_order_id": "C-new"})
    return calls


def _off(account, market="KR"):
    store.set_market_state(account, market, "disabled")


def test_market_on_reanalyzes_a_stale_symbol_then_keeps_the_plan(monkeypatch, sent, rearm):
    _off("robo")
    pid = _plan("robo")
    old = (date.today() - timedelta(days=10)).isoformat()
    dates = {"KR:005930": old}
    monkeypatch.setattr(analyze, "latest_date", lambda sid: dates.get(sid, today()))
    asked = []

    def reanalyze(symbols):
        asked.append(list(symbols))
        dates["KR:005930"] = today()
    monkeypatch.setattr(execute, "_reanalyze", reanalyze)
    monkeypatch.setattr(broker, "quote", lambda sid: 72000.0)

    rep = execute.market_on("robo", "KR")

    assert asked == [["KR:005930"]]
    assert rep["market"] == "KR" and rep["reanalyzed"] == ["KR:005930"]
    assert rep["kept"] == [pid]
    assert plans.get(pid)["conditional_order_id"] == "C-new" and rearm == [("KR:005930", "buy")]
    assert store.market_state("robo", "KR") == "active"
    assert store.last_resume("robo", "KR") == rep
    assert store.last_resume("robo", "US") is None
    assert "유지 1" in sent[-1]


def test_market_on_keeps_and_flags_when_reanalysis_fails(monkeypatch, sent, rearm):
    _off("robo")
    pid = _plan("robo")
    monkeypatch.setattr(analyze, "latest_date", lambda sid: None)
    monkeypatch.setattr(execute, "_reanalyze", lambda symbols: None)
    monkeypatch.setattr(broker, "quote", lambda sid: 72000.0)
    rep = execute.market_on("robo", "KR")
    assert rep["reanalysis_failed"] == [pid] and rep["kept"] == []
    p = plans.get(pid)
    assert p["status"] == "armed" and "[resume] reanalysis failed, kept" in p["note"]
    assert p["conditional_order_id"] is None                     # not judged, not armed


@pytest.mark.parametrize("quote, over, key, status", [
    (65000.0, {"expires_at": 1}, "expired", "expired"),
    (59000.0, {}, "thesis_broken", "abandoned"),
    (81000.0, {}, "target_passed", "abandoned"),
    (None, {}, "quote_unavailable", "armed"),
])
def test_market_on_rules(monkeypatch, sent, rearm, quote, over, key, status):
    _off("robo")
    pid = _plan("robo", **over)
    monkeypatch.setattr(broker, "quote", lambda sid: quote)
    rep = execute.market_on("robo", "KR")
    assert rep[key] == [pid]
    assert plans.get(pid)["status"] == status


def test_market_on_never_closes_an_exit_for_staleness(monkeypatch, sent, rearm):
    _off("robo")
    _hold("robo")
    stop = _exit("robo")
    held = _plan("robo", sid="KR:000660", status="entered")
    monkeypatch.setattr(analyze, "latest_date", lambda sid: None)
    rep = execute.market_on("robo", "KR")
    assert plans.get(stop)["status"] == "armed" and plans.get(held)["status"] == "entered"
    assert sorted(rearm) == [("KR:000660", "sell"), ("KR:005930", "sell")]
    assert rep["reanalyzed"] == []


def test_market_on_touches_only_its_market(monkeypatch, sent, rearm):
    _off("robo", "KR")
    _off("robo", "US")
    kr = _plan("robo")
    us = _plan("robo", sid="US:AAPL", price=150,
               conditions={"entry": {"kind": "below", "price": 150}})
    monkeypatch.setattr(broker, "quote", lambda sid: 72000.0)
    rep = execute.market_on("robo", "KR")
    assert rep["kept"] == [kr]
    assert plans.get(us)["conditional_order_id"] is None
    assert store.market_state("robo", "US") == "disabled"


def test_market_on_paper_registers_nothing(monkeypatch, sent):
    _off(PAPER)
    pid = _plan(PAPER)
    monkeypatch.setattr(broker, "quote", lambda sid: 72000.0)
    rep = execute.market_on(PAPER, "KR")
    assert rep["kept"] == [pid] and plans.get(pid)["conditional_order_id"] is None


def test_analysis_staleness_counts_trading_days_not_calendar_days(monkeypatch):
    """Friday's analysis on Monday is one trading day old — still fresh."""
    monkeypatch.setattr(broker, "is_trading_day",
                        lambda market, ymd=None: date(int(ymd[:4]), int(ymd[4:6]),
                                                      int(ymd[6:])).weekday() < 5)
    monday = date(2026, 9, 7)
    fri = (monday - timedelta(days=3)).isoformat()
    monkeypatch.setattr(analyze, "latest_date", lambda sid: fri)
    assert not execute._analysis_stale("KR:005930", today=monday)
    assert execute._analysis_stale("KR:005930", today=monday + timedelta(days=2))


# ── the cycle ────────────────────────────────────────────────────────────

def test_cycle_skips_an_account_whose_focus_market_is_off(monkeypatch, sent):
    monkeypatch.setattr(cycle_job, "_phase0_data", lambda ids: {"ok": True})
    monkeypatch.setattr(cycle_job.session, "run_main",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")))
    store.set_trade_mode("robo", "off")
    _off(PAPER, "US")
    sent.clear()
    assert cycle_job.run_cycle("evening") == 0
    assert store.last_cycle(PAPER) is None
    assert len(sent) == 1 and "US" in sent[0]


def test_the_brief_sees_only_active_markets(monkeypatch):
    _hold(PAPER, "KR:005930")
    _hold(PAPER, "US:AAPL", qty=1, price=150)
    _plan(PAPER, sid="KR:000660")
    _plan(PAPER, sid="US:MSFT", price=400)
    store.watch_add(PAPER, "KR:035420", "지켜봄")
    store.watch_add(PAPER, "US:NVDA", "지켜봄")
    _off(PAPER, "KR")
    ctx = session.build_main_context(PAPER)
    for sid in ("US:AAPL", "US:MSFT", "US:NVDA"):
        assert sid in ctx, sid
    for sid in ("KR:005930", "KR:000660", "KR:035420"):
        assert sid not in ctx, sid
    assert "KRW" not in ctx.split("[예수금(DB)]")[1].split("\n")[0]
    assert "운용 시장: US" in ctx


def test_analysis_set_leaves_out_a_disabled_market(monkeypatch):
    _hold(PAPER, "KR:005930")
    _plan(PAPER, sid="US:MSFT", price=400)
    _off(PAPER, "KR")
    assert jobs.to_analyze() == ["US:MSFT"]
    _off("robo", "KR")                                  # robo has nothing held
    assert "KR:005930" not in jobs.to_analyze()


def test_market_off_mid_cycle_leaves_the_new_buy_unarmed(monkeypatch, sent, rearm):
    """A cycle runs for hours. A market turned off after the plan is written
    but before its entry watch is registered still means no new entry: the
    plan stays (its reasoning is the record) and turning the market back on
    re-judges it."""
    monkeypatch.setattr(cycle_job, "_phase0_data", lambda ids: {"ok": True})
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

    def create_then_off(*a, **k):
        pid = real_create(*a, **k)
        store.set_market_state("robo", "KR", "disabled")       # the operator, mid-cycle
        return pid
    monkeypatch.setattr(cycle_job.plan_ledger, "create", create_then_off)

    assert cycle_job.run_account("robo", "morning") == 0
    cyc = store.last_cycle("robo")
    assert cyc["status"] == "ok" and rearm == []
    (p,) = plans.active("robo")
    assert p["status"] == "armed" and p["conditional_order_id"] is None
    skips = [e for e in store.journal_recent("robo", cycle_id=cyc["cycle_id"])
             if e["kind"] == "market_off_skip"]
    assert [e["payload"]["plan_id"] for e in skips] == [p["plan_id"]]

    monkeypatch.setattr(broker, "quote", lambda sid: 72000.0)
    rep = execute.market_on("robo", "KR")
    assert rep["kept"] == [p["plan_id"]] and rearm == [("KR:005930", "buy")]


def test_execute_orders_skips_buys_in_a_disabled_market_but_sends_sells(monkeypatch, sent):
    store.upsert_position("robo", "KR:000660", qty=3, avg_cost=100000, currency="KRW")
    _off("robo")
    placed = []
    monkeypatch.setattr(broker, "place_order",
                        lambda sid, side, qty, price, order_type:
                        placed.append((sid, side)) or {"ok": True, "order_no": "O1"})
    cid = store.cycle_start("robo", "morning", "KR", dry_run=False)
    res = execute.execute_orders("robo", cid, [
        {"symbol_id": "KR:005930", "side": "buy", "qty": 1, "price": 70000,
         "rationale": "신규"},
        {"symbol_id": "KR:000660", "side": "sell", "qty": 3, "price": 120000,
         "rationale": "청산"},
    ], dry_run=False)
    assert placed == [("KR:000660", "sell")]
    assert [r["status"] for r in res] == ["market_off_skip", "submitted"]
    assert [o["side"] for o in store.orders_recent("robo")] == ["sell"]
    skips = [e for e in store.journal_recent("robo", cycle_id=cid)
             if e["kind"] == "market_off_skip"]
    assert len(skips) == 1 and skips[0]["payload"]["symbol_id"] == "KR:005930"


# ── API ──────────────────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(security, "_SECRET_FILE", tmp_path / ".secret")
    monkeypatch.setattr(security, "_secret_cache", None)
    monkeypatch.setattr(qapi, "_price", lambda sid: 10000.0)
    monkeypatch.setattr(qapi, "_name", lambda sid: "테스트종목")
    # The job runs inline so the test reads its result without polling.
    monkeypatch.setattr(analysis_jobs, "start",
                        lambda key, fn: fn() and {"state": "started"} or {"state": "started"})
    store.set_pin_hash("robo", security.hash_pin("robo", PIN))
    app = FastAPI()
    app.include_router(qapi.router)
    return TestClient(app, cookies={qapi.COOKIE_NAME: security.make_session_token("robo")})


def test_api_market_toggle_round_trip(client, monkeypatch, sent):
    monkeypatch.setattr(broker, "quote", lambda sid: 72000.0)
    q = "?account=robo_paper"
    url = "/api/quant/markets/KR" + q
    assert client.post(url, json={"state": "disabled"}).status_code == 403   # PIN step-up
    assert client.post("/api/quant/markets/JP" + q,
                       json={"state": "disabled", "pin": PIN}).status_code == 404
    assert client.post(url, json={"state": "paused", "pin": PIN}).status_code == 400
    st = client.get("/api/quant/state" + q).json()
    assert st["markets"]["KR"]["state"] == "active"

    r = client.post(url, json={"state": "disabled", "pin": PIN})
    assert r.status_code == 200 and r.json()["to"] == "disabled"
    assert client.post(url, json={"state": "disabled", "pin": PIN}).status_code == 400
    st = client.get("/api/quant/state" + q).json()
    assert st["markets"]["KR"]["state"] == "disabled"
    assert st["markets"]["US"]["state"] == "active"
    assert client.get("/api/quant/state").json()["markets"]["KR"]["state"] == "active"  # robo untouched

    r = client.post(url, json={"state": "active", "pin": PIN})
    assert r.status_code == 200 and r.json()["job"]["state"] == "started"
    st = client.get("/api/quant/state" + q).json()
    assert st["markets"]["KR"]["state"] == "active"
    assert st["markets"]["KR"]["last_resume"]["market"] == "KR"
    s = client.get("/api/quant/markets/KR/status" + q).json()
    assert "state" in s and s["report"]["market"] == "KR"

    for gone in ("/api/quant/pause", "/api/quant/resume"):
        assert client.post(gone, json={"pin": PIN}).status_code in (404, 405)


def test_api_refuses_a_second_turn_on_while_one_runs(client, monkeypatch):
    store.set_market_state(PAPER, "KR", "disabled")
    monkeypatch.setattr(analysis_jobs, "get_status", lambda key: {"state": "running"})
    r = client.post("/api/quant/markets/KR?account=robo_paper",
                    json={"state": "active", "pin": PIN})
    assert r.status_code == 400
