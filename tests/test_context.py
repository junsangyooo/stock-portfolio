"""The account context assembler — src/ledger/context.py.

Everything the plan writer must not compute for itself is computed here, so
these tests are about arithmetic and shape, not about judgment: weights that
close against cash, distances that keep their sign, a stale analysis that says
so, and the same bytes out for the same book in.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

import pytest

from src.core.kst import KST
from src.ledger import context, schema

TODAY = "2026-09-07"
# Every stored timestamp hangs off this instant, not off the wall clock: an
# age asserted against time.time() is an age that changes tomorrow.
NOON = datetime(2026, 9, 7, 12, 0, tzinfo=KST)


def _ts(days_ago: float) -> int:
    return int((NOON - timedelta(days=days_ago)).timestamp())


def _d(offset: int) -> str:
    return (datetime.strptime(TODAY, "%Y-%m-%d")
            - timedelta(days=offset)).strftime("%Y-%m-%d")


def _analysis(date: str, *, invalidation=None, target=None, label="buy",
              status="ok") -> dict:
    body = {"date": date, "status": status, "label": label, "score": 0.6,
            "confidence": 0.7, "summary_ko": "요약",
            "levels": {"current": 71000.0,
                       "entry_zone": {"low": 69000.0, "high": 71000.0,
                                      "rationale_ko": "버려진다"},
                       "support": [{"price": 69500.0, "basis_ko": "버려진다"}],
                       "resistance": [{"price": 75000.0}]},
            "outlook": {"horizon": "near",
                        "scenarios": [{"case": "base", "narrative_ko": "버려진다",
                                       "confirm": ["c1"], "invalidate": ["i1"]}]},
            "reassess_when": ["실적 발표"],
            "observations": [{"fact_ko": "이건 넘어가지 않는다"}]}
    if invalidation is not None:
        body["levels"]["invalidation"] = {"price": invalidation}
    if target is not None:
        body["levels"]["targets"] = [{"price": target}]
    return body


PRICES = {"KR:AAA": 72000.0, "KR:BBB": 18000.0, "US:CCC": 120.0,
          "KR:DDD": 5000.0}
ANALYSES = {
    "KR:AAA": _analysis(_d(0), invalidation=66000.0, target=82000.0),
    "KR:BBB": _analysis(_d(2), label="sell"),
    "US:CCC": _analysis(_d(5)),
    "KR:DDD": _analysis(_d(1), status="facts_only", label="neutral"),
}


class _Q:
    def __init__(self, price):
        self.price, self.prev_close, self.ts = price, price, 0


@pytest.fixture
def book(tmp_path, monkeypatch):
    """A two-currency account with holdings, a watch row, plans and trades."""
    conn = sqlite3.connect(tmp_path / "book.db")
    schema.apply(conn)
    now = _ts(0)
    conn.execute(
        "INSERT INTO accounts (account_id, name, kind, base_currency,"
        " sort_order, note, created_at) VALUES"
        " ('acc', '테스트', 'human', 'KRW', 1, '장기 보유 계좌', ?)", (now,))
    conn.executemany(
        "INSERT INTO holdings (account_id, symbol_id, name, qty, avg_cost,"
        " currency, note, opened_at, added_at, sort_order, updated_at)"
        " VALUES ('acc', ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)", [
            ("KR:AAA", None, 10, 70000.0, None, None, _ts(98), now, now),
            ("KR:BBB", None, 5, 20000.0, None, None, None, now, now),
            ("US:CCC", None, 2, 100.0, "USD", None, None, now, now),
            ("KR:DDD", None, 0, None, None, "배당 보고 산다", None, now, now),
        ])
    conn.execute(
        "INSERT INTO watch (account_id, symbol_id, reason, updated_at)"
        " VALUES ('acc', 'KR:EEE', '신규 진입 후보', ?)", (now,))
    conn.executemany(
        "INSERT INTO plans (account_id, symbol_id, name, side, price, qty,"
        " note, status, source, conditions, expires_at, created_at,"
        " updated_at) VALUES ('acc', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [
            ("KR:AAA", "AAA", "sell", 80000.0, 5, "익절", "armed", "user",
             '{"take_profit": {"kind": "above", "price": 80000}}',
             None, _ts(20), now),
            ("KR:BBB", "BBB", "buy", 17000.0, 3, "", "proposed", "agent",
             None, _ts(-10), now, now),
            ("KR:AAA", "AAA", "buy", 60000.0, 1, "옛것", "abandoned", "agent",
             None, None, _ts(60), now),
        ])
    conn.executemany(
        "INSERT INTO cash (account_id, currency, amount, updated_at)"
        " VALUES ('acc', ?, ?, ?)",
        [("KRW", 100000.0, now), ("USD", 500.0, now)])
    # one inside the 14-day window, one outside
    for off, side in ((3, "buy"), (30, "sell")):
        conn.execute(
            "INSERT INTO trades (account_id, symbol_id, side, qty, price,"
            " traded_at, created_at) VALUES ('acc', 'KR:AAA', ?, 1, 70000, ?, ?)",
            (side, _ts(off), now))
    conn.commit()

    monkeypatch.setattr(context.quote_store, "get",
                        lambda sid, **k: (_Q(PRICES[sid]) if sid in PRICES
                                          else None))
    monkeypatch.setattr(context.analyze, "latest",
                        lambda sid: ANALYSES.get(sid))
    monkeypatch.setattr(context.theme_store, "get",
                        lambda sid: ([{"theme_id": "semiconductor",
                                       "relation": "primary", "weight": 1.0},
                                      {"theme_id": "ai_sw",
                                       "relation": "related", "weight": 0.5}]
                                     if sid == "KR:AAA" else []))
    monkeypatch.setattr(context.uni_search, "get", lambda sid: {
        "name_kr": sid.split(":")[1], "currency":
        "USD" if sid.startswith("US:") else "KRW"})
    try:
        yield conn
    finally:
        conn.close()


def test_weights_close_against_cash_in_each_currency(book):
    ctx = context.build(book, "acc", today=TODAY)
    for ccy in ("KRW", "USD"):
        t = ctx["totals"][ccy]
        invested = sum(h["weight_pct"] for h in ctx["holdings"]
                       if h["currency"] == ccy)
        assert round(invested + t["cash_pct"], 1) == 100.0
    krw = ctx["totals"]["KRW"]
    assert krw == {"eval": 810000.0, "cost": 800000.0, "pnl": 10000.0,
                   "pnl_pct": 1.25, "cash": 100000.0, "cash_known": True,
                   "total_assets": 910000.0, "cash_pct": 10.99,
                   "n_held": 2, "n_cost_unknown": 0}
    assert ctx["totals"]["USD"]["pnl_pct"] == 20.0


def test_holdings_are_heaviest_first_with_per_position_pnl(book):
    ctx = context.build(book, "acc", today=TODAY)
    assert [h["symbol_id"] for h in ctx["holdings"]] == \
        ["KR:AAA", "US:CCC", "KR:BBB"]
    aaa = ctx["holdings"][0]
    assert aaa["pnl_pct"] == 2.86 and aaa["weight_pct"] == 79.12
    assert aaa["days_held"] == 98
    assert aaa["plan_ids"] == [1]              # the abandoned one is not active


def test_distance_to_a_level_keeps_its_sign(book):
    a = context.build(book, "acc", today=TODAY)["holdings"][0]["analysis"]
    assert a["dist_to_invalidation_pct"] == -8.33   # stop sits below
    assert a["dist_to_target_pct"] == 13.89         # target sits above
    # a symbol whose analysis has no invalidation gets null, not zero
    bbb = [h for h in context.build(book, "acc", today=TODAY)["holdings"]
           if h["symbol_id"] == "KR:BBB"][0]
    assert bbb["analysis"]["dist_to_invalidation_pct"] is None


def test_freshness_has_three_answers_and_the_old_ones_are_listed(book):
    ctx = context.build(book, "acc", today=TODAY)
    got = {h["symbol_id"]: h["analysis"]["freshness"] for h in ctx["holdings"]}
    assert got == {"KR:AAA": "fresh", "KR:BBB": "stale", "US:CCC": "missing"}
    whys = {(m["symbol_id"], m["why"]) for m in ctx["missing"]}
    assert ("US:CCC", "analysis_old") in whys
    assert ("KR:EEE", "analysis_missing") in whys   # nothing stored at all
    assert ("KR:EEE", "quote_missing") in whys
    assert ("KR:AAA", "analysis_missing") not in whys


def test_a_zero_quantity_row_is_interest_not_a_position(book):
    ctx = context.build(book, "acc", today=TODAY)
    assert [w["symbol_id"] for w in ctx["watch"]] == ["KR:DDD", "KR:EEE"]
    ddd = ctx["watch"][0]
    assert ddd["reason"] == "배당 보고 산다"
    assert ddd["analysis"]["status"] == "facts_only"   # not dressed as a verdict
    assert "KR:DDD" not in [h["symbol_id"] for h in ctx["holdings"]]
    assert ctx["totals"]["KRW"]["n_held"] == 2


def test_only_active_plans_travel_and_they_carry_their_conditions(book):
    ctx = context.build(book, "acc", today=TODAY)
    assert [p["plan_id"] for p in ctx["plans"]] == [1, 2]
    user_plan = ctx["plans"][0]
    assert user_plan["source"] == "user" and user_plan["age_days"] == 20
    assert user_plan["conditions"]["take_profit"]["price"] == 80000
    assert ctx["plans"][1]["status"] == "proposed"
    assert ctx["plans"][1]["expires"] is not None


def test_recent_trades_stop_at_the_window(book):
    ctx = context.build(book, "acc", today=TODAY)
    assert [t["side"] for t in ctx["recent_trades"]] == ["buy"]


def test_concentration_scales_by_membership_weight(book):
    conc = context.build(book, "acc", today=TODAY)["concentration"]
    # AAA is 79.12% of the KRW book: primary 1.0 → 79.1, related 0.5 → 39.6
    assert conc["KRW"] == {"semiconductor": 79.1, "ai_sw": 39.6}
    assert "USD" not in conc          # CCC has no theme rows


def test_the_analysis_body_is_trimmed_to_what_a_plan_needs(book):
    a = context.build(book, "acc", today=TODAY)["holdings"][0]["analysis"]
    assert set(a["levels"]) == {"current", "entry_zone", "invalidation",
                                "targets", "support", "resistance"}
    assert a["levels"]["support"] == [{"price": 69500.0}]      # no basis_ko
    assert a["scenarios"] == [{"case": "base", "confirm": ["c1"],
                               "invalidate": ["i1"]}]          # no narrative
    assert "observations" not in a and "counter" not in a


def test_two_builds_of_one_book_are_byte_identical(book):
    a = context.as_json(context.build(book, "acc", today=TODAY))
    b = context.as_json(context.build(book, "acc", today=TODAY))
    assert a == b
    assert json.loads(a)["account"]["note"] == "장기 보유 계좌"


def test_an_unknown_account_raises_rather_than_planning_an_empty_book(book):
    with pytest.raises(KeyError):
        context.build(book, "nope", today=TODAY)


def test_a_plan_on_a_symbol_the_book_neither_holds_nor_watches_is_resolved(book):
    """quant has six such rows. The model must judge every active plan, so the
    symbol has to arrive with its price and its coordinates — or with a
    missing[] row saying why it did not."""
    now = _ts(0)
    book.execute(
        "INSERT INTO plans (account_id, symbol_id, name, side, price, qty,"
        " status, source, created_at, updated_at)"
        " VALUES ('acc', 'KR:FFF', 'FFF', 'buy', 1000, 1, 'armed', 'agent',"
        " ?, ?)", (now, now))
    book.commit()
    ctx = context.build(book, "acc", today=TODAY)
    entry = [w for w in ctx["watch"] if w["symbol_id"] == "KR:FFF"]
    assert entry and entry[0]["reason"] == "활성 계획만 있는 종목"
    assert ("KR:FFF", "analysis_missing") in {(m["symbol_id"], m["why"])
                                              for m in ctx["missing"]}
    assert "KR:FFF" in {p["symbol_id"] for p in ctx["plans"]}


def test_a_us_symbol_watched_without_a_stored_name_is_not_a_bare_ticker(book,
                                                                       monkeypatch):
    """The universe leaves name_kr NULL for US symbols, and the agent's own
    watch rows carry no name — so the English rung is the only one left."""
    now = _ts(0)
    book.execute("INSERT INTO watch (account_id, symbol_id, reason,"
                 " updated_at) VALUES ('acc', 'US:GOOGL', '후보', ?)", (now,))
    book.commit()
    monkeypatch.setattr(context.uni_search, "get", lambda sid: {
        "name_kr": None, "name_en": "Alphabet Inc.", "currency": "USD"})
    ctx = context.build(book, "acc", today=TODAY)
    googl = [w for w in ctx["watch"] if w["symbol_id"] == "US:GOOGL"][0]
    assert googl["name"] == "Alphabet Inc."


def test_a_position_with_no_cost_basis_is_counted_not_hidden(book):
    """It still belongs to eval — it is what the account is worth — but pnl
    cannot include it, so the count says so instead of pnl quietly ceasing to
    mean eval - cost."""
    now = _ts(0)
    book.execute(
        "INSERT INTO holdings (account_id, symbol_id, qty, avg_cost, note,"
        " added_at, sort_order, updated_at)"
        " VALUES ('acc', 'KR:DDD', 4, NULL, NULL, ?, 0, ?)"
        " ON CONFLICT(account_id, symbol_id) DO UPDATE SET qty = 4,"
        " avg_cost = NULL", (now, now))
    book.commit()
    t = context.build(book, "acc", today=TODAY)["totals"]["KRW"]
    assert t["n_held"] == 3 and t["n_cost_unknown"] == 1
    assert t["eval"] == 810000.0 + 4 * 5000.0
    assert t["cost"] == 800000.0            # unchanged: we do not know it


def test_a_negative_cost_basis_is_not_a_percentage(book):
    """A sign typo in the holdings form is reachable; a +1500 gain must not
    reach the model as a 300% loss."""
    now = _ts(0)
    book.execute("UPDATE holdings SET avg_cost = -50 WHERE symbol_id = 'KR:BBB'")
    book.commit()
    ctx = context.build(book, "acc", today=TODAY)
    bbb = [h for h in ctx["holdings"] if h["symbol_id"] == "KR:BBB"][0]
    assert bbb["pnl_pct"] is None
    t = ctx["totals"]["KRW"]
    # the poisoned row leaves the aggregate entirely rather than dragging it
    assert t["cost"] == 700000.0 and t["pnl"] == 20000.0
    assert t["pnl_pct"] == 2.86 and t["n_cost_unknown"] == 1
