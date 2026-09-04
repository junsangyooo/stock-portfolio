"""trades — what the person actually did, and what that does to the book.

One recorded trade moves four things in one transaction (ops notes §3.1):

  trades     a new row
  holdings   buy: qty +=, avg_cost = weighted mean
             sell: qty -=, avg_cost unchanged; a full sell deletes the row
             (the trade rows keep the history)
  cash       buy: −(price·qty + fee)   sell: +(price·qty − fee)
  plans      the trade row is what transitions plan.status —
             buy: armed → entered     sell (full): armed sell / entered → done
             (a partial sell links the trade to the plan, status unchanged)

Which plan? An explicit plan_id wins (and must be this account's, same
symbol). Otherwise exactly one matching active plan transitions; two or more
transition nothing — the trade is still recorded and the candidates are
returned so the screen can ask, because guessing closes the wrong plan.

quant does not share this: its holdings truth is the broker reconcile
(execute.reconcile), so a portfolio-only module is the honest place.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from typing import List, Optional

from ...core.kst import KST
from .db import connect
from .plan_store import _row as _plan_row

SIDES = ("buy", "sell")


def _candidates(c, account_id: str, symbol_id: str, side: str) -> list:
    if side == "buy":
        rows = c.execute(
            "SELECT * FROM plans WHERE account_id = ? AND symbol_id = ? "
            "AND side = 'buy' AND status = 'armed'", (account_id, symbol_id))
    else:
        rows = c.execute(
            "SELECT * FROM plans WHERE account_id = ? AND symbol_id = ? "
            "AND ((side = 'sell' AND status = 'armed') OR status = 'entered')",
            (account_id, symbol_id))
    return [dict(r) for r in rows.fetchall()]


def _matches(plan: dict, side: str) -> bool:
    """The same predicate _candidates uses, for an explicit plan_id: a buy
    fills an armed buy plan; a sell exits an armed sell plan or an entered
    one. Anything else is the wrong plan, not a transition."""
    if side == "buy":
        return plan["side"] == "buy" and plan["status"] == "armed"
    return (plan["side"] == "sell" and plan["status"] == "armed") \
        or plan["status"] == "entered"


def record(account_id: str, *, symbol_id: str, side: str, qty: float,
           price: float, currency: str, fee: float = 0.0,
           name: Optional[str] = None, traded_at: Optional[int] = None,
           plan_id: Optional[int] = None, note: Optional[str] = None) -> dict:
    """Raises ValueError for a bad trade (→400), LookupError when plan_id is
    not this account's (→404). Nothing is written on either."""
    side = (side or "").lower()
    if side not in SIDES:
        raise ValueError("side must be 'buy' or 'sell'")
    if not qty or qty <= 0:
        raise ValueError("qty must be > 0")
    if not price or price <= 0:
        raise ValueError("price must be > 0")
    if fee is None or fee < 0:
        raise ValueError("fee must be >= 0")
    now = int(time.time())
    traded_at = int(traded_at) if traded_at is not None else now
    currency = (currency or "").upper()

    with connect() as c:
        try:
            # One transaction from the first read: the holdings SELECT below
            # decides new_qty, so a concurrent trade must wait here, not read
            # the same old_qty and double-credit cash / skip the oversell check.
            c.execute("BEGIN IMMEDIATE")
            # ── plan resolution (before any write, so a bad id costs nothing)
            plan = None
            candidates: list = []
            if plan_id is not None:
                row = c.execute(
                    "SELECT * FROM plans WHERE plan_id = ? AND account_id = ?",
                    (plan_id, account_id)).fetchone()
                if row is None:
                    raise LookupError("plan not found")
                if row["symbol_id"] != symbol_id:
                    raise ValueError("plan is for another symbol")
                plan = dict(row)
                if not _matches(plan, side):
                    raise ValueError("plan does not take this trade")
            else:
                candidates = _candidates(c, account_id, symbol_id, side)
                if len(candidates) == 1:
                    plan = candidates[0]
                    candidates = []

            # ── holdings
            h = c.execute(
                "SELECT qty, avg_cost FROM holdings "
                "WHERE account_id = ? AND symbol_id = ?",
                (account_id, symbol_id)).fetchone()
            old_qty = float(h["qty"] or 0) if h else 0.0
            old_avg = float(h["avg_cost"]) if h and h["avg_cost"] is not None else None
            if side == "buy":
                new_qty = old_qty + qty
                # shares held at an unknown cost are averaged at this buy's
                # price, not at 0 — 0 would understate the book's cost basis
                base = old_avg if (old_avg is not None and old_qty > 0) else price
                new_avg = (base * old_qty + price * qty) / new_qty
            else:
                if qty > old_qty + 1e-9:
                    raise ValueError("cannot sell more than held")
                new_qty = old_qty - qty
                if new_qty <= 1e-9:          # float dust (0.1+0.2 ≠ 0.3) is flat
                    new_qty = 0.0
                new_avg = old_avg if new_qty > 0 else None
            removed = side == "sell" and new_qty <= 0
            # entry_* is written when the position opens and left alone after —
            # a changed quantity does not change why it was bought. A full sell
            # takes the row with it, so the next buy starts from nothing.
            opening = side == "buy" and old_qty <= 0
            if removed:
                c.execute("DELETE FROM holdings WHERE account_id = ? AND symbol_id = ?",
                          (account_id, symbol_id))
            else:
                # sort_order = now, like holding_store.upsert: a new card goes
                # to the end, not above every reordered one (0 would)
                c.execute(
                    """
                    INSERT INTO holdings
                      (account_id, symbol_id, name, qty, avg_cost, currency,
                       entry_plan_id, entry_rationale, opened_at,
                       added_at, sort_order, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(account_id, symbol_id) DO UPDATE SET
                      qty=excluded.qty, avg_cost=excluded.avg_cost,
                      currency=COALESCE(holdings.currency, excluded.currency),
                      entry_plan_id=COALESCE(excluded.entry_plan_id, holdings.entry_plan_id),
                      entry_rationale=COALESCE(excluded.entry_rationale, holdings.entry_rationale),
                      opened_at=COALESCE(excluded.opened_at, holdings.opened_at),
                      updated_at=excluded.updated_at
                    """,
                    (account_id, symbol_id, None, new_qty, new_avg, currency,
                     plan["plan_id"] if (opening and plan) else None,
                     (plan.get("note") or None) if (opening and plan) else None,
                     traded_at if opening else None,
                     now, now, now))

            # ── cash (same sign convention as balance_store.adjust)
            delta = -(price * qty + fee) if side == "buy" else (price * qty - fee)
            c.execute(
                """
                INSERT INTO cash (account_id, currency, amount, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(account_id, currency) DO UPDATE SET
                  amount=cash.amount + excluded.amount,
                  updated_at=excluded.updated_at
                """, (account_id, currency, float(delta), now))
            cash_balance = float(c.execute(
                "SELECT amount FROM cash WHERE account_id = ? AND currency = ?",
                (account_id, currency)).fetchone()["amount"])

            # ── plan transition. The trade is linked to the matched plan
            # either way; only a fill / a full exit moves the status.
            new_status = None
            if plan is not None:
                if side == "buy" and plan["status"] == "armed":
                    new_status = "entered"
                elif side == "sell" and new_qty <= 0:
                    new_status = "done"
            if new_status:
                c.execute("UPDATE plans SET status = ?, updated_at = ? "
                          "WHERE plan_id = ?", (new_status, now, plan["plan_id"]))
                plan["status"] = new_status

            # ── the trade itself
            cur = c.execute(
                """
                INSERT INTO trades (account_id, symbol_id, name, side, qty,
                                    price, fee, currency, traded_at, plan_id,
                                    source, note, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'manual', ?, ?)
                """,
                (account_id, symbol_id, name, side, float(qty), float(price),
                 float(fee), currency, traded_at,
                 plan["plan_id"] if plan else None, note or None, now))
            trade = dict(c.execute("SELECT * FROM trades WHERE trade_id = ?",
                                   (cur.lastrowid,)).fetchone())
            c.commit()
        except BaseException:
            c.rollback()
            raise

    return {
        "trade": trade,
        "holding": {"qty": new_qty, "avg_cost": new_avg, "removed": removed},
        "cash_currency": currency,
        "cash_balance": cash_balance,
        "plan": _plan_row(plan) if plan else None,
        "plan_candidates": [p["plan_id"] for p in candidates],
    }


def _kst_day(day: str) -> datetime:
    """'YYYY-MM-DD' → midnight KST. ValueError on anything else (→400)."""
    d = date.fromisoformat(day)          # strict: '2026-9-1' is refused
    return datetime(d.year, d.month, d.day, tzinfo=KST)


def list_by_account(account_id: str, limit: int = 200, *,
                    symbol_id: Optional[str] = None,
                    since: Optional[str] = None,
                    until: Optional[str] = None) -> List[dict]:
    """Newest first. `since`/`until` are KST calendar days, both inclusive,
    against `traded_at`."""
    where, args = ["account_id = ?"], [account_id]
    if symbol_id:
        where.append("symbol_id = ?")
        args.append(symbol_id)
    if since:
        where.append("traded_at >= ?")
        args.append(int(_kst_day(since).timestamp()))
    if until:
        where.append("traded_at < ?")
        args.append(int((_kst_day(until) + timedelta(days=1)).timestamp()))
    args.append(int(limit))
    with connect() as c:
        rows = c.execute(
            f"SELECT * FROM trades WHERE {' AND '.join(where)} "
            "ORDER BY traded_at DESC, trade_id DESC LIMIT ?", args).fetchall()
        return [dict(r) for r in rows]
