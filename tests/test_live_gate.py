"""Kill switch — mode='off' is absolute; --live (dry_run=False) cannot pierce it.

Both cron entries (keeper, cycle_job) must refuse to run anything, log it,
and hand back a non-zero exit when a flag was passed and ignored.
"""
import sys

import pytest

from src.quant import cycle_job, db as qdb, execute, keeper, plans, store


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(qdb, "QUANT_DB_PATH", tmp_path / "quant.db")
    monkeypatch.setattr(qdb, "_initialized", False)
    qdb.forget_account_id()
    store.create_account("robo", "로보")   # mode defaults to off


@pytest.fixture
def _count_orders(monkeypatch):
    calls = []
    monkeypatch.setattr(execute, "execute_orders",
                        lambda cid, orders, **k: calls.append(orders)
                        or [{**o, "status": "dry_run"} for o in orders])
    monkeypatch.setattr(execute, "arm_conditional", lambda *a, **k: {})
    return calls


def _armed_plan():
    return plans.create(symbol_id="KR:005930", name="삼성전자", side="buy",
                        qty=1, price=70000,
                        conditions={"entry": {"kind": "immediate"}})


def test_keeper_off_ignores_forced_live(monkeypatch, _count_orders):
    _armed_plan()
    monkeypatch.setattr(keeper, "market_open", lambda m, now: True)
    monkeypatch.setattr(keeper, "_quote", lambda sid: 69000.0)

    out = keeper.run(dry_run=False)
    assert out == {"skipped": "mode_off"}
    assert _count_orders == []

    # the same tick runs once the account is switched on
    store.set_trade_mode("dry")
    out = keeper.run(dry_run=None)
    assert out["fired"] == 1


def test_keeper_main_exits_nonzero_when_live_flag_is_ignored(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["keeper", "--live"])
    assert keeper.main() == 1
    monkeypatch.setattr(sys, "argv", ["keeper"])
    assert keeper.main() == 0          # plain cron tick on an off account


def test_cycle_off_ignores_forced_live(monkeypatch, _count_orders):
    def _no_cycle(*a, **k):
        raise AssertionError("cycle started on an off account")
    monkeypatch.setattr(store, "cycle_start", _no_cycle)

    assert cycle_job.run_cycle("morning", dry_run=False) == 1
    assert cycle_job.run_cycle("morning", dry_run=None) == 0
    assert _count_orders == []


def test_cycle_never_abandons_the_persons_plan(monkeypatch):
    """The allocator may list any active plan in abandon_plans; a plan the
    person wrote (source='user') is journaled and left armed, and its
    broker watch is not cancelled."""
    cancelled = []
    monkeypatch.setattr(execute, "cancel_conditionals",
                        lambda cid, ids, **k: cancelled.extend(ids))
    mine = plans.create(symbol_id="KR:005930", name="s", side="buy", qty=1, price=1,
                        conditions={"entry": {"kind": "immediate"}}, source="user")
    theirs = plans.create(symbol_id="KR:000660", name="h", side="buy", qty=1, price=1,
                          conditions={"entry": {"kind": "immediate"}})
    plans.set_conditional(mine, "C1")
    cid = store.cycle_start("morning", "KR", dry_run=True)
    cycle_job._abandon(cid, [mine, theirs], dry_run=True)
    assert plans.get(mine)["status"] == "armed"
    assert plans.get(theirs)["status"] == "abandoned"
    assert cancelled == []
    seen = {(e["payload"]["plan_id"], e["payload"]["abandoned"])
            for e in store.journal_recent(limit=10) if e["kind"] == "watch"}
    assert {(mine, False), (theirs, True)} <= seen
    assert "[사용자]" in plans.for_context()          # the brief says whose it is
