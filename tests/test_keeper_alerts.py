"""Keeper never fails silently — a skipped quote or a refused order reaches
Discord (one message per tick) and the journal.
"""
import pytest

from src.core.notify import discord
from src.quant import db as qdb, execute, keeper, plans, store


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(qdb, "QUANT_DB_PATH", tmp_path / "quant.db")
    monkeypatch.setattr(qdb, "_initialized", False)
    store.create_account("robo", "로보")
    monkeypatch.setattr(keeper, "market_open", lambda m, now: True)
    # Nothing here may reach Toss: local secrets make the facade live.
    monkeypatch.setattr(execute, "arm_conditional", lambda *a, **k: {})
    monkeypatch.setattr(execute, "_submit",
                        lambda *a, **k: {"ok": False, "error": "refused"})


@pytest.fixture
def sent(monkeypatch):
    msgs = []
    monkeypatch.setattr(discord, "send",
                        lambda text, *a, **k: msgs.append(text) or True)
    return msgs


def _alerts():
    return [e for e in store.journal_recent("robo") if e["kind"] == "alert"]


def _armed(sid="KR:005930"):
    return plans.create("robo", symbol_id=sid, name="n", side="buy", qty=1,
                        price=70000,
                        conditions={"entry": {"kind": "below", "price": 70000}})


def test_quote_none_is_counted_and_alerted(monkeypatch, sent):
    store.set_trade_mode("robo", "dry")
    pid = _armed()
    monkeypatch.setattr(keeper, "_quote", lambda sid: None)

    out = keeper.run()
    assert out["fired"] == 0                       # skip is preserved
    assert plans.get(pid)["status"] == "armed"
    assert len(sent) == 1 and "KR:005930" in sent[0]
    assert _alerts()[0]["payload"]["quote_none"] == [f"plan#{pid} KR:005930"]


def test_rejected_then_duplicate_are_alerted(monkeypatch, sent):
    store.set_trade_mode("robo", "live")
    pid = _armed()
    monkeypatch.setattr(keeper, "_quote", lambda sid: 69000.0)

    keeper.run()
    assert plans.get(pid)["status"] == "armed"
    assert len(sent) == 1 and "rejected" in sent[0] and f"plan#{pid}" in sent[0]
    assert _alerts()[0]["payload"]["rejected"] == [f"plan#{pid} KR:005930"]

    # same day, same salt → idem hit; the silence would otherwise last all day.
    # Different content from tick 1 (rejected → duplicate_skipped), so the
    # repeat-suppression does not apply and this is a second message.
    keeper.run()
    assert len(sent) == 2 and "duplicate_skipped" in sent[1]
    assert _alerts()[0]["payload"]["duplicate_skipped"] == [f"plan#{pid} KR:005930"]


def test_order_record_error_is_alerted(monkeypatch, sent):
    store.set_trade_mode("robo", "live")
    pid = _armed()
    monkeypatch.setattr(keeper, "_quote", lambda sid: 69000.0)
    monkeypatch.setattr(execute, "execute_orders",
                        lambda aid, cid, orders, **k: [{**o, "status": "error",
                                                   "error": "db locked"}
                                                  for o in orders])
    keeper.run()
    assert len(sent) == 1 and "error" in sent[0]
    assert _alerts()[0]["payload"]["error"] == [f"plan#{pid} KR:005930"]


def test_clean_tick_sends_nothing(monkeypatch, sent):
    store.set_trade_mode("robo", "dry")
    _armed()
    monkeypatch.setattr(keeper, "_quote", lambda sid: 69000.0)
    keeper.run()
    assert sent == [] and _alerts() == []


def test_rejected_entry_does_not_arm_the_exit_pair(monkeypatch, sent):
    store.set_trade_mode("robo", "live")
    _armed()
    monkeypatch.setattr(keeper, "_quote", lambda sid: 69000.0)
    armed = []
    monkeypatch.setattr(execute, "arm_conditional",
                        lambda *a, **k: armed.append(a) or {})
    keeper.run()                                   # _submit refuses (fixture)
    assert armed == []


def test_one_plan_raising_does_not_stop_the_others(monkeypatch, sent):
    store.set_trade_mode("robo", "dry")
    bad, good = _armed("KR:005930"), _armed("KR:000660")

    def quote(sid):
        if sid == "KR:005930":
            raise RuntimeError("quote feed down")
        return 69000.0
    monkeypatch.setattr(keeper, "_quote", quote)

    out = keeper.run()
    assert out["fired"] == 1
    assert plans.get(good)["status"] == "entered"
    assert plans.get(bad)["status"] == "armed"
    assert len(sent) == 1 and "RuntimeError: quote feed down" in sent[0]
    assert _alerts()[0]["payload"]["error"] == [
        f"plan#{bad} RuntimeError: quote feed down"]


def test_repeated_issue_is_sent_on_the_first_and_every_sixth_tick(monkeypatch, sent):
    store.set_trade_mode("robo", "dry")
    _armed()
    monkeypatch.setattr(keeper, "_quote", lambda sid: None)
    for _ in range(7):
        keeper.run()
    assert len(sent) == 2                          # tick 1 and tick 7
    assert len(_alerts()) == 7                     # the journal keeps every tick


def test_rejected_reason_lands_in_the_plan_note(monkeypatch, sent):
    store.set_trade_mode("robo", "live")
    pid = _armed()
    monkeypatch.setattr(keeper, "_quote", lambda sid: 69000.0)
    keeper.run()                                   # _submit refuses (fixture)
    p = plans.get(pid)
    assert p["status"] == "armed"
    assert "[keeper] entry rejected: refused" in (p["note"] or "")
