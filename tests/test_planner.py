"""The plan writer — src/ledger/planner.py.

The model is allowed to be wrong; it is not allowed to be wrong *into the
book*. So these tests are almost all about the gate between the two: what
validation keeps, what it refuses, and what a refusal leaves behind.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

import pytest

from src.core.kst import KST
from src.ledger import context, planner, schema
from src.research import llm_backend

TODAY = "2026-09-07"
NOON = datetime(2026, 9, 7, 12, 0, tzinfo=KST)


def _ts(days_ago: float) -> int:
    return int((NOON - timedelta(days=days_ago)).timestamp())


def _analysis(*, invalidation=66000.0, target=82000.0, date=TODAY):
    return {"date": date, "status": "ok", "label": "buy", "score": 0.6,
            "confidence": 0.7, "summary_ko": "요약",
            "levels": {"current": 72000.0,
                       "entry_zone": {"low": 69000.0, "high": 71000.0},
                       "invalidation": {"price": invalidation},
                       "targets": [{"price": target}],
                       "support": [{"price": 69500.0}],
                       "resistance": [{"price": 75000.0}]},
            "outlook": {"scenarios": []}, "reassess_when": []}


ANALYSES = {"KR:AAA": _analysis(), "KR:BBB": _analysis(),
            "KR:CCC": _analysis(), "KR:OLD": _analysis(date="2026-08-01")}
PRICES = {"KR:AAA": 72000.0, "KR:BBB": 18000.0, "KR:CCC": 5000.0,
          "KR:OLD": 1000.0}


class _Q:
    def __init__(self, price):
        self.price, self.prev_close, self.ts = price, price, 0


@pytest.fixture
def book(tmp_path, monkeypatch):
    conn = sqlite3.connect(tmp_path / "book.db")
    schema.apply(conn)
    now = _ts(0)
    conn.execute(
        "INSERT INTO accounts (account_id, name, kind, base_currency,"
        " sort_order, created_at) VALUES ('acc', '테스트', 'human', 'KRW', 1, ?)",
        (now,))
    conn.executemany(
        "INSERT INTO holdings (account_id, symbol_id, qty, avg_cost, note,"
        " added_at, sort_order, updated_at) VALUES ('acc', ?, ?, ?, NULL,"
        " ?, 0, ?)",
        [("KR:AAA", 10, 70000.0, now, now), ("KR:BBB", 0, None, now, now),
         ("KR:CCC", 4, 4000.0, now, now), ("KR:OLD", 5, 900.0, now, now)])
    conn.execute("INSERT INTO cash (account_id, currency, amount, updated_at)"
                 " VALUES ('acc', 'KRW', 300000.0, ?)", (now,))
    conn.executemany(
        "INSERT INTO plans (account_id, symbol_id, name, side, price, qty,"
        " note, status, source, created_at, updated_at)"
        " VALUES ('acc', ?, ?, ?, ?, ?, ?, 'armed', ?, ?, ?)",
        [("KR:AAA", "AAA", "sell", 80000.0, 5, "익절", "agent", now, now),
         ("KR:AAA", "AAA", "buy", 60000.0, 1, "내 계획", "user", now, now)])
    conn.commit()

    monkeypatch.setattr(context.quote_store, "get",
                        lambda sid, **k: (_Q(PRICES[sid]) if sid in PRICES
                                          else None))
    monkeypatch.setattr(context.analyze, "latest", lambda sid: ANALYSES.get(sid))
    monkeypatch.setattr(context.theme_store, "get", lambda sid: [])
    monkeypatch.setattr(context.uni_search, "get",
                        lambda sid: {"name_kr": sid.split(":")[1],
                                     "currency": "KRW"})
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def ctx(book):
    return context.build(book, "acc", today=TODAY)


def _doc(**over) -> dict:
    """A diff that judges both active plans and notes every symbol."""
    doc = {"keep": [1], "modify": [], "abandon": [], "suggest": [{"plan_id": 2,
           "what": "익절 상향", "why": "타깃이 올라갔다"}],
           "add": [], "reanalyze": [],
           "notes_by_symbol": {s: {"stance": "hold", "line_ko": "유지"}
                               for s in ("KR:AAA", "KR:BBB", "KR:CCC",
                                         "KR:OLD")},
           "portfolio_note": "변경 없음"}
    doc.update(over)
    return doc


def _answer(monkeypatch, *docs):
    """Feed the CLI a canned answer per attempt."""
    seq = list(docs)
    calls = []

    def _call(self, system, user, **k):
        calls.append(user)
        d = seq.pop(0) if seq else seq
        if isinstance(d, str):
            return d
        return "여기 있다\n```json\n" + json.dumps(d, ensure_ascii=False) + "\n```"

    monkeypatch.setattr(llm_backend.ClaudeCliBackend, "call", _call)
    monkeypatch.setattr(llm_backend.ClaudeCliBackend, "__init__",
                        lambda self, model=None: setattr(self, "model", model))
    return calls


# ── completeness ─────────────────────────────────────────────────────────

def test_an_unjudged_active_plan_refuses_the_whole_diff(ctx):
    with pytest.raises(planner.IncompleteDiff):
        planner.validate(_doc(suggest=[]), ctx)      # plan 2 now unmentioned


def test_a_plan_judged_twice_refuses_the_whole_diff(ctx):
    with pytest.raises(planner.IncompleteDiff):
        planner.validate(_doc(keep=[1, 2]), ctx)     # 2 is also in suggest


def test_a_symbol_with_no_line_gets_one_rather_than_an_empty_card(ctx):
    accepted, rejected = planner.validate(
        _doc(notes_by_symbol={"KR:AAA": {"stance": "hold", "line_ko": "유지"}}),
        ctx)
    assert accepted["notes_by_symbol"]["KR:BBB"] == {"stance": "none",
                                                     "line_ko": "(판단 없음)"}
    assert sum(1 for r in rejected if "notes_by_symbol" in r["item"]) == 3


def test_an_unknown_stance_falls_back_to_none(ctx):
    accepted, _ = planner.validate(
        _doc(notes_by_symbol={s: {"stance": "yolo", "line_ko": "간다"}
                              for s in ("KR:AAA", "KR:BBB", "KR:CCC",
                                        "KR:OLD")}), ctx)
    assert accepted["notes_by_symbol"]["KR:AAA"]["stance"] == "none"
    assert accepted["notes_by_symbol"]["KR:AAA"]["line_ko"] == "간다"


# ── whose plan is it ─────────────────────────────────────────────────────

def test_a_persons_own_plan_is_not_the_agents_to_rewrite(ctx):
    accepted, rejected = planner.validate(
        _doc(suggest=[], abandon=[{"plan_id": 2, "why": "쓸모없다"}]), ctx)
    assert accepted["abandon"] == []
    assert any("person's own plan" in r["why"] for r in rejected)


def test_a_plan_that_is_not_active_is_dropped_not_applied(ctx):
    accepted, rejected = planner.validate(
        _doc(abandon=[{"plan_id": 99, "why": "?"}]), ctx)
    assert accepted["abandon"] == []
    assert any("no such active plan" in r["why"] for r in rejected)


# ── prices and coordinates ───────────────────────────────────────────────

def test_a_price_on_a_real_coordinate_is_anchored(ctx):
    accepted, _ = planner.validate(_doc(add=[{
        "symbol_id": "KR:BBB", "side": "buy", "qty": 2, "price": 17000,
        "conditions": {"stop": {"kind": "below", "price": 66000}},
        "basis": {"stop": "invalidation"}, "why": "손절이 좌표 위"}]), ctx)
    assert accepted["add"][0]["unanchored"] is False


def test_an_invented_price_is_kept_but_flagged(ctx):
    """A person filters these, so it is marked, not dropped."""
    accepted, _ = planner.validate(_doc(add=[{
        "symbol_id": "KR:BBB", "side": "buy", "qty": 2, "price": 17000,
        "conditions": {"stop": {"kind": "below", "price": 12345}},
        "basis": {"stop": "invalidation"}, "why": "지어낸 가격"}]), ctx)
    assert accepted["add"][0]["unanchored"] is True


def test_basis_none_is_flagged_too(ctx):
    accepted, _ = planner.validate(_doc(add=[{
        "symbol_id": "KR:BBB", "side": "buy", "qty": 2, "price": 17000,
        "conditions": {"stop": {"kind": "below", "price": 66000}},
        "basis": {"stop": "none"}, "why": "좌표 없음"}]), ctx)
    assert accepted["add"][0]["unanchored"] is True


def test_an_unreadable_condition_drops_the_item(ctx):
    accepted, rejected = planner.validate(_doc(add=[{
        "symbol_id": "KR:BBB", "side": "buy", "qty": 2, "price": 17000,
        "conditions": {"stop": {"kind": "when_i_feel_like_it"}},
        "why": "keeper가 못 읽는다"}]), ctx)
    assert accepted["add"] == []
    assert any("stop.kind" in r["why"] for r in rejected)


# ── new plans ────────────────────────────────────────────────────────────

def test_a_new_plan_needs_a_symbol_this_account_knows(ctx):
    accepted, rejected = planner.validate(_doc(add=[{
        "symbol_id": "KR:ZZZ", "side": "buy", "qty": 1, "price": 100,
        "why": "처음 보는 종목"}]), ctx)
    assert accepted["add"] == []
    assert any("neither held nor watched" in r["why"] for r in rejected)


def test_a_second_plan_on_a_planned_symbol_is_a_modify(ctx):
    accepted, rejected = planner.validate(_doc(add=[{
        "symbol_id": "KR:AAA", "side": "buy", "qty": 1, "price": 70000,
        "why": "이미 계획이 있다"}]), ctx)
    assert accepted["add"] == []
    assert any("already has an active plan" in r["why"] for r in rejected)


def test_a_stale_analysis_carries_no_new_plan(ctx):
    accepted, rejected = planner.validate(_doc(add=[{
        "symbol_id": "KR:OLD", "side": "buy", "qty": 1, "price": 1000,
        "why": "분석이 한 달 전"}]), ctx)
    assert accepted["add"] == []
    assert any("no usable analysis" in r["why"] for r in rejected)


def test_a_fractional_quantity_is_not_a_quantity(ctx):
    accepted, rejected = planner.validate(_doc(add=[{
        "symbol_id": "KR:BBB", "side": "buy", "qty": 1.5, "price": 17000,
        "why": "반 주"}]), ctx)
    assert accepted["add"] == []
    assert any("whole number" in r["why"] for r in rejected)



def test_a_sell_is_clamped_to_what_is_held(ctx):
    """KR:CCC is held 4 with a fresh analysis and no plan — the only shape
    that reaches the clamp."""
    accepted, _ = planner.validate(_doc(add=[{
        "symbol_id": "KR:CCC", "side": "sell", "qty": 999, "price": 5000,
        "why": "전량"}]), ctx)
    assert accepted["add"][0]["qty"] == 4


def test_buys_beyond_the_currencys_cash_come_off_the_end(ctx):
    """The prompt asks for descending confidence, so the tail is what goes."""
    accepted, rejected = planner.validate(_doc(add=[
        {"symbol_id": "KR:BBB", "side": "buy", "qty": 10, "price": 18000,
         "why": "180,000 — 든다"},
        {"symbol_id": "KR:BBB", "side": "buy", "qty": 10, "price": 18000,
         "why": "또 180,000 — 현금 300,000을 넘는다"}]), ctx)
    assert len(accepted["add"]) == 1
    assert any("cannot cover" in r["why"] for r in rejected)


def test_an_expiry_in_the_past_is_no_expiry(ctx):
    accepted, _ = planner.validate(_doc(add=[{
        "symbol_id": "KR:BBB", "side": "buy", "qty": 1, "price": 17000,
        "expires": "2026-01-01", "why": "이미 지났다"}]), ctx)
    assert accepted["add"][0]["expires_at"] is None


# ── applying ─────────────────────────────────────────────────────────────

def test_an_accepted_plan_lands_as_a_proposal_nobody_executes(book, ctx):
    accepted, _ = planner.validate(_doc(add=[{
        "symbol_id": "KR:BBB", "side": "buy", "qty": 2, "price": 17000,
        "conditions": {"entry": {"kind": "below", "price": 17000}},
        "basis": {"entry": "none"}, "why": "진입"}]), ctx)
    planner.apply(book, "acc", accepted, add_status="proposed", today=TODAY)
    row = book.execute(
        "SELECT status, source, note, conditions FROM plans"
        " WHERE symbol_id = 'KR:BBB'").fetchone()
    assert row[0] == "proposed" and row[1] == "agent"
    assert row[2].startswith(f"[agent {TODAY}]") and "[unanchored]" in row[2]
    assert json.loads(row[3])["entry"]["price"] == 17000
    # nothing an executor looks at changed
    assert book.execute("SELECT count(*) FROM plans WHERE status = 'armed'"
                        ).fetchone()[0] == 2


def test_a_modify_that_names_no_conditions_keeps_the_ones_it_has(book, ctx):
    book.execute("UPDATE plans SET conditions = ? WHERE plan_id = 1",
                 (json.dumps({"stop": {"kind": "below", "price": 60000}}),))
    book.commit()
    fresh = context.build(book, "acc", today=TODAY)
    accepted, _ = planner.validate(
        _doc(keep=[], modify=[{"plan_id": 1, "qty": 3, "why": "수량만"}]), fresh)
    planner.apply(book, "acc", accepted, add_status="proposed", today=TODAY)
    row = book.execute("SELECT qty, conditions FROM plans WHERE plan_id = 1"
                       ).fetchone()
    assert row[0] == 3
    assert json.loads(row[1])["stop"]["price"] == 60000


def test_a_failure_midway_leaves_the_book_untouched(book, ctx, monkeypatch):
    accepted, _ = planner.validate(_doc(add=[{
        "symbol_id": "KR:BBB", "side": "buy", "qty": 2, "price": 17000,
        "why": "진입"}], abandon=[]), ctx)
    accepted["abandon"] = [{"plan_id": 1}]        # no "why" → raises mid-apply
    before = book.execute("SELECT count(*) FROM plans").fetchone()[0]
    with pytest.raises(KeyError):
        planner.apply(book, "acc", accepted, add_status="proposed",
                      today=TODAY)
    assert book.execute("SELECT count(*) FROM plans").fetchone()[0] == before


# ── the pass ─────────────────────────────────────────────────────────────

def test_a_pass_writes_one_row_and_the_plans(book, monkeypatch):
    _answer(monkeypatch, _doc(add=[{
        "symbol_id": "KR:BBB", "side": "buy", "qty": 2, "price": 17000,
        "conditions": {"entry": {"kind": "below", "price": 17000}},
        "basis": {"entry": "none"}, "why": "진입"}]))
    out = planner.run(book, "acc", slot="morning", today=TODAY)
    assert out["status"] == "ok" and out["n_add"] == 1 and out["applied"]
    row = book.execute("SELECT status, notes_by_symbol, portfolio_note,"
                       " context_chars FROM plan_runs").fetchone()
    assert row[0] == "ok"
    assert set(json.loads(row[1])) == {"KR:AAA", "KR:BBB", "KR:CCC", "KR:OLD"}
    assert row[2] == "변경 없음" and row[3] > 0


def test_a_dry_pass_records_but_does_not_touch_the_plans(book, monkeypatch):
    _answer(monkeypatch, _doc(add=[{
        "symbol_id": "KR:BBB", "side": "buy", "qty": 2, "price": 17000,
        "why": "진입"}]))
    before = book.execute("SELECT count(*) FROM plans").fetchone()[0]
    out = planner.run(book, "acc", slot="manual", dry=True, today=TODAY)
    assert out["status"] == "ok" and out["applied"] is False
    assert book.execute("SELECT count(*) FROM plans").fetchone()[0] == before
    assert book.execute("SELECT slot FROM plan_runs").fetchone()[0] == "manual"


def test_the_morning_slot_runs_once_a_day(book, monkeypatch):
    calls = _answer(monkeypatch, _doc(), _doc())
    planner.run(book, "acc", slot="morning", today=TODAY)
    out = planner.run(book, "acc", slot="morning", today=TODAY)
    assert out["status"] == "skipped" and len(calls) == 1


def test_a_manual_preview_replaces_the_last_one(book, monkeypatch):
    _answer(monkeypatch, _doc(), _doc(portfolio_note="두 번째"))
    planner.run(book, "acc", slot="manual", dry=True, today=TODAY)
    planner.run(book, "acc", slot="manual", dry=True, today=TODAY)
    rows = book.execute("SELECT portfolio_note FROM plan_runs"
                        " WHERE slot = 'manual'").fetchall()
    assert len(rows) == 1 and rows[0][0] == "두 번째"


def test_unparseable_output_is_retried_once_then_recorded(book, monkeypatch):
    calls = _answer(monkeypatch, "JSON 없이 말만 한다", "이번에도 없다")
    out = planner.run(book, "acc", slot="morning", today=TODAY)
    assert out["status"] == "parse_error" and len(calls) == 2
    assert book.execute("SELECT status FROM plan_runs").fetchone()[0] == \
        "parse_error"


def test_an_incomplete_diff_is_retried_and_the_second_one_can_stand(book,
                                                                   monkeypatch):
    calls = _answer(monkeypatch, _doc(suggest=[]), _doc())
    out = planner.run(book, "acc", slot="morning", today=TODAY)
    assert out["status"] == "ok" and len(calls) == 2


def test_a_dead_cli_is_a_row_not_an_exception(book, monkeypatch):
    def _boom(self, *a, **k):
        raise RuntimeError("claude CLI AUTH error")
    monkeypatch.setattr(llm_backend.ClaudeCliBackend, "call", _boom)
    monkeypatch.setattr(llm_backend.ClaudeCliBackend, "__init__",
                        lambda self, model=None: None)
    out = planner.run(book, "acc", slot="morning", today=TODAY)
    assert out["status"] == "llm_error"
    assert book.execute("SELECT status FROM plan_runs").fetchone()[0] == \
        "llm_error"
    assert book.execute("SELECT count(*) FROM plans WHERE source = 'agent'"
                        " AND status = 'proposed'").fetchone()[0] == 0


def test_a_proposal_nobody_approved_expires(book, monkeypatch):
    book.execute(
        "INSERT INTO plans (account_id, symbol_id, name, side, price, qty,"
        " status, source, created_at, updated_at)"
        " VALUES ('acc', 'KR:BBB', 'BBB', 'buy', 1, 1, 'proposed', 'agent',"
        " ?, ?)", (_ts(8), _ts(8)))
    book.commit()
    _answer(monkeypatch, _doc())
    planner.run(book, "acc", slot="morning", today=TODAY)
    assert book.execute("SELECT status FROM plans WHERE symbol_id = 'KR:BBB'"
                        ).fetchone()[0] == "expired"


def test_the_pass_never_sends_another_accounts_book(book, monkeypatch):
    calls = _answer(monkeypatch, _doc())
    book.execute(
        "INSERT INTO accounts (account_id, name, kind, base_currency,"
        " sort_order, created_at) VALUES ('other', '남', 'human', 'KRW', 2, ?)",
        (_ts(0),))
    book.execute("INSERT INTO holdings (account_id, symbol_id, qty, avg_cost,"
                 " added_at, sort_order, updated_at)"
                 " VALUES ('other', 'KR:SECRET', 1, 1, ?, 0, ?)",
                 (_ts(0), _ts(0)))
    book.commit()
    planner.run(book, "acc", slot="morning", today=TODAY)
    assert "KR:SECRET" not in calls[0] and "other" not in calls[0]


def test_an_exit_only_plan_takes_its_price_from_its_own_numbers(ctx):
    """The first real pass left `price` null on all 19 plans: an exit-only
    plan has no entry price to name. The number is in the plan already."""
    accepted, rejected = planner.validate(_doc(add=[{
        "symbol_id": "KR:BBB", "side": "sell", "qty": 1, "price": None,
        "conditions": {"take_profit": {"kind": "above", "price": 21000},
                       "stop": {"kind": "below", "price": 16000}},
        "basis": {"take_profit": "targets[0]"}, "why": "출구만 건다"}]), ctx)
    assert rejected == [] or all("price" not in r["why"] for r in rejected)
    assert accepted["add"] == [] or accepted["add"][0]["price"] == 21000


def test_a_buy_with_an_entry_condition_prices_from_the_entry(ctx):
    accepted, _ = planner.validate(_doc(add=[{
        "symbol_id": "KR:BBB", "side": "buy", "qty": 1,
        "conditions": {"entry": {"kind": "below", "price": 17500}},
        "why": "진입가가 기준가"}]), ctx)
    assert accepted["add"][0]["price"] == 17500


def test_a_plan_with_no_number_anywhere_is_still_refused(ctx, monkeypatch):
    accepted, rejected = planner.validate(_doc(add=[{
        "symbol_id": "KR:BBB", "side": "buy", "qty": 1,
        "conditions": {"entry": {"kind": "immediate"}}, "why": "숫자가 없다"}]),
        ctx)
    # KR:BBB's analysis carries levels.current, so that is what it falls back to
    assert accepted["add"][0]["price"] == 72000.0


def test_a_fractional_quantity_says_so_and_not_something_about_price(ctx):
    _, rejected = planner.validate(_doc(add=[{
        "symbol_id": "KR:BBB", "side": "buy", "qty": 1.5, "price": 17000,
        "why": "반 주"}]), ctx)
    assert any(r["why"] == "qty must be a whole number above zero"
               for r in rejected)


def test_the_models_own_html_escaping_does_not_reach_the_reader(ctx):
    """The first real pass wrote "TIGER 미국S&amp;P500" from a context holding a
    plain "&". The card renders with textContent, so the entity would show."""
    accepted, _ = planner.validate(
        _doc(portfolio_note="TIGER 미국S&amp;P500 정리",
             suggest=[{"plan_id": 2, "what": "A &amp; B", "why": "x &lt; y"}]),
        ctx)
    assert accepted["portfolio_note"] == "TIGER 미국S&P500 정리"
    assert accepted["suggest"][0]["what"] == "A & B"
    assert accepted["suggest"][0]["why"] == "x < y"
