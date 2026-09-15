"""One account's situation, assembled from its own book.

The planner is a judgment step, so everything it could get wrong by doing
arithmetic is done here instead: weights, P&L, the distance to a level, theme
concentration. The model quotes these; it does not compute them.

Takes a connection rather than opening one, because the ledger is one schema
in two files — the same assembler serves portfolio.db and quant.db and must
not know which one it is holding. It reads shared resources (quotes, the
stored analysis, theme membership) but nothing here reaches back into an app
(ops notes §4).

No LLM, no network, no writes. Two calls with the same inputs produce the same
JSON, byte for byte, so a diff between two days is a diff in the book.

    from src.ledger import context
    with db.connect() as conn:
        ctx = context.build(conn, "junisa")
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta

from ..core.kst import KST
from ..core.kst import today as _kst_today
from ..market.store import quote_store, theme_store
from ..research import analyze
from ..universe import search as uni_search

# How old a stored analysis may be and still carry a plan. `stale` still
# plans, but the prompt makes the model say the age out loud; `missing` does
# not plan at all.
#
# The ceiling follows the refresh cadence instead of fighting it. A held
# symbol is re-analysed when something actually changed on it and otherwise
# after 7 trading days (research/analyze/due.MAX_AGE_COLD) — about 11 calendar
# days. A lower ceiling here would drop every quiet holding out of the plan
# pass while its analysis was still true: nothing had happened to it.
FRESH_DAYS = 1
STALE_DAYS = 12

RECENT_TRADE_DAYS = 14
TOP_THEMES = 8

# The plans a new pass is allowed to see. Everything else is history: a done
# or abandoned plan re-read as "active" would be re-judged forever.
ACTIVE_PLAN_STATUSES = ("armed", "entered", "proposed")


# ── small helpers ─────────────────────────────────────────────────────────

def _rows(conn: sqlite3.Connection, sql: str, args=()) -> list[dict]:
    """Rows as plain dicts, whatever row_factory the caller's book set."""
    cur = conn.execute(sql, args)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _as_date(s: str) -> date | None:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _epoch_to_date(ts) -> str | None:
    """An epoch second to the KST calendar day it fell on."""
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts), KST).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return None


def _age_days(when: str | None, today: date) -> int | None:
    d = _as_date(when) if when else None
    return (today - d).days if d else None


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None          # NaN is not a number we can quote


def _r(v, n: int = 2) -> float | None:
    f = _num(v)
    return round(f, n) if f is not None else None


def _pct_from(price: float | None, level: float | None) -> float | None:
    """How far `level` sits from `price`, signed, in percent."""
    if price is None or level is None or price == 0:
        return None
    return round((level - price) / price * 100, 2)


# ── analysis ──────────────────────────────────────────────────────────────

def _price_list(items) -> list[dict]:
    out = []
    for it in items or []:
        p = _r(it.get("price")) if isinstance(it, dict) else _r(it)
        if p is not None:
            out.append({"price": p})
    return out


def _levels(lv: dict) -> dict:
    """The coordinates a plan may quote, and nothing else.

    A stored level carries its anchoring evidence and a rationale; the planner
    is told not to re-argue the analysis, so it gets the numbers only.
    """
    out: dict = {}
    cur = _r(lv.get("current"))
    if cur is not None:
        out["current"] = cur
    ez = lv.get("entry_zone") or {}
    lo, hi = _r(ez.get("low")), _r(ez.get("high"))
    if lo is not None or hi is not None:
        out["entry_zone"] = {"low": lo, "high": hi}
    inv = _r((lv.get("invalidation") or {}).get("price"))
    if inv is not None:
        out["invalidation"] = {"price": inv}
    out["targets"] = _price_list(lv.get("targets"))
    out["support"] = _price_list(lv.get("support"))
    out["resistance"] = _price_list(lv.get("resistance"))
    return out


def _analysis(symbol_id: str, price: float | None, today: date) -> dict | None:
    """The stored analysis, trimmed to what a plan writer uses.

    Observations, readings and the counter-case are left out on purpose: this
    step does not re-open the judgment, it applies it to a book.
    """
    body = analyze.latest(symbol_id)
    if body is None:
        return None
    age = _age_days(body.get("date"), today)
    freshness = ("missing" if age is None or age > STALE_DAYS
                 else "fresh" if age <= FRESH_DAYS else "stale")
    levels = _levels(body.get("levels") or {})
    targets = levels.get("targets") or []
    return {
        "date": body.get("date"),
        "age_days": age,
        "freshness": freshness,
        "status": body.get("status"),
        "label": body.get("label"),
        "score": _r(body.get("score")),
        "confidence": _r(body.get("confidence")),
        "summary_ko": body.get("summary_ko"),
        "levels": levels,
        "scenarios": [
            {"case": s.get("case"),
             "confirm": s.get("confirm") or [],
             "invalidate": s.get("invalidate") or []}
            for s in ((body.get("outlook") or {}).get("scenarios") or [])
            if isinstance(s, dict)
        ],
        "reassess_when": body.get("reassess_when") or [],
        "dist_to_invalidation_pct": _pct_from(
            price, (levels.get("invalidation") or {}).get("price")),
        "dist_to_target_pct": _pct_from(
            price, targets[0]["price"] if targets else None),
    }


# ── the assembler ─────────────────────────────────────────────────────────

def build(conn: sqlite3.Connection, account_id: str, *,
          today: str | None = None) -> dict:
    """This account, today, as the plan writer sees it.

    Raises KeyError when the account is not in this book — a caller that hands
    over the wrong id should hear about it rather than plan an empty account.
    """
    day = today or _kst_today()
    today_d = _as_date(day) or datetime.now(KST).date()

    acc_rows = _rows(conn, "SELECT * FROM accounts WHERE account_id = ?",
                     (account_id,))
    if not acc_rows:
        raise KeyError(f"no such account in this book: {account_id}")
    acc = acc_rows[0]
    base_ccy = acc.get("base_currency") or "KRW"

    holding_rows = _rows(
        conn,
        "SELECT symbol_id, name, qty, avg_cost, currency, note, opened_at,"
        "       entry_rationale"
        "  FROM holdings WHERE account_id = ? ORDER BY symbol_id",
        (account_id,))
    plan_rows = _rows(
        conn,
        "SELECT plan_id, symbol_id, name, side, qty, price, note, status,"
        "       source, conditions, expires_at, created_at"
        f"  FROM plans WHERE account_id = ? AND status IN "
        f"({','.join('?' * len(ACTIVE_PLAN_STATUSES))})"
        "  ORDER BY plan_id",
        (account_id, *ACTIVE_PLAN_STATUSES))
    watch_rows = _rows(
        conn,
        "SELECT symbol_id, name, reason, target_price"
        "  FROM watch WHERE account_id = ? ORDER BY symbol_id",
        (account_id,))
    cash = {r["currency"]: _num(r["amount"]) or 0.0 for r in _rows(
        conn, "SELECT currency, amount FROM cash WHERE account_id = ?",
        (account_id,))}

    plans_by_symbol: dict[str, list[int]] = {}
    for p in plan_rows:
        plans_by_symbol.setdefault(p["symbol_id"], []).append(p["plan_id"])

    # ── holdings and watch ────────────────────────────────────────────────
    # A human book marks interest with a qty-0 holding row (the card grid
    # already draws it that way); the auto book uses the `watch` table. Both
    # land in watch[], so the prompt has one place to look.
    held: list[dict] = []
    watch: list[dict] = []
    missing: list[dict] = []
    totals: dict[str, dict] = {}

    def _bucket(ccy: str) -> dict:
        # cash starts as None, not 0.0: an account with no row in `cash` has
        # not told us it is empty, it has told us nothing. They were the same
        # value here, so a book whose balance was never entered read as broke
        # and every buy came back rejected for "cash 0" — a fact nobody had
        # established. `cash_known` is what the prompt and the guard read.
        return totals.setdefault(ccy, {
            "eval": 0.0, "cost": 0.0, "pnl": 0.0, "pnl_pct": 0.0,
            "cash": None, "cash_known": False,
            "total_assets": 0.0, "cash_pct": 0.0,
            "n_held": 0, "n_cost_unknown": 0})

    def _resolve(sid: str, name_override=None, ccy_override=None):
        """Name, currency, price and analysis for one symbol — and a row in
        missing[] for each of those we could not get. Every symbol the model
        sees goes through here, so 'we do not know' is never silent."""
        meta = uni_search.get(sid) or {}
        q = quote_store.get(sid, max_age_s=quote_store.STALE_S)
        price = _r(q.price) if q else None
        ana = _analysis(sid, price, today_d)
        if price is None:
            missing.append({"symbol_id": sid, "why": "quote_missing"})
        if ana is None:
            missing.append({"symbol_id": sid, "why": "analysis_missing"})
        elif ana["freshness"] == "missing":
            missing.append({"symbol_id": sid, "why": "analysis_old"})
        name = (name_override or meta.get("name_kr") or meta.get("name_en")
                or sid)
        ccy = ccy_override or meta.get("currency") or base_ccy
        return name, ccy, price, ana

    for h in holding_rows:
        sid = h["symbol_id"]
        qty = _num(h.get("qty")) or 0.0
        avg = _num(h.get("avg_cost"))
        name, ccy, price, ana = _resolve(sid, h.get("name"), h.get("currency"))

        if qty <= 0:
            watch.append({
                "symbol_id": sid, "name": name, "currency": ccy,
                "price": price, "reason": h.get("note"), "analysis": ana})
            continue

        t = _bucket(ccy)
        t["n_held"] += 1
        if price is not None:
            # eval carries the position whatever we know about its cost — it
            # is what the account is worth, and weights are measured against
            # it. cost and pnl cannot, so the count says how many are absent
            # rather than letting pnl quietly stop meaning eval - cost.
            t["eval"] += price * qty
            if avg is not None and avg > 0:
                t["cost"] += avg * qty
                t["pnl"] += (price - avg) * qty
            else:
                # A missing cost basis and an impossible one (a sign typo in
                # the holdings form is reachable) are the same thing here: we
                # cannot price what this position cost. One such row left in
                # would poison the whole currency's pnl_pct.
                t["n_cost_unknown"] += 1
        held.append({
            "symbol_id": sid, "name": name, "currency": ccy,
            "qty": qty, "avg_cost": avg, "price": price,
            "pnl_pct": (round((price - avg) / avg * 100, 2)
                        if price is not None and avg and avg > 0 else None),
            "weight_pct": None,                     # filled once totals close
            "note": h.get("note"),
            "opened_at": _epoch_to_date(h.get("opened_at")),
            "days_held": _age_days(_epoch_to_date(h.get("opened_at")), today_d),
            "plan_ids": plans_by_symbol.get(sid, []),
            "analysis": ana,
        })

    known = {w["symbol_id"] for w in watch} | {h["symbol_id"] for h in held}
    for w in watch_rows:
        sid = w["symbol_id"]
        if sid in known:
            continue
        name, ccy, price, ana = _resolve(sid, w.get("name"))
        watch.append({"symbol_id": sid, "name": name, "currency": ccy,
                      "price": price, "reason": w.get("reason"),
                      "analysis": ana})
        known.add(sid)

    # A plan can name a symbol the book neither holds nor watches — quant has
    # six such rows today. Without this sweep that plan reaches the model with
    # no price, no coordinates and nothing in missing[] to say so, and the
    # model is required to judge every active plan.
    for sid in sorted(set(plans_by_symbol) - known):
        name, ccy, price, ana = _resolve(
            sid, next(p["name"] for p in plan_rows if p["symbol_id"] == sid))
        watch.append({"symbol_id": sid, "name": name, "currency": ccy,
                      "price": price, "reason": "활성 계획만 있는 종목",
                      "analysis": ana})

    # ── totals ────────────────────────────────────────────────────────────
    for ccy, amt in cash.items():
        b = _bucket(ccy)
        b["cash"], b["cash_known"] = amt, True
    _bucket(base_ccy)   # the account's own currency answers even when empty
    for ccy, t in totals.items():
        c = t["cash"] or 0.0
        t["total_assets"] = t["eval"] + c
        t["pnl_pct"] = (round(t["pnl"] / t["cost"] * 100, 2)
                        if t["cost"] > 0 else 0.0)
        t["cash_pct"] = (round(c / t["total_assets"] * 100, 2)
                         if (t["cash_known"] and t["total_assets"]) else 0.0)
        for k in ("eval", "cost", "pnl", "cash", "total_assets"):
            if t[k] is not None:
                t[k] = round(t[k], 2)
        if not t["cash_known"]:
            missing.append({"symbol_id": f"({ccy})", "why": "cash_unknown"})

    for h in held:
        ta = totals.get(h["currency"], {}).get("total_assets") or 0
        if h["price"] is not None and ta:
            h["weight_pct"] = round(h["price"] * h["qty"] / ta * 100, 2)
    held.sort(key=lambda h: (-(h["weight_pct"] or 0), h["symbol_id"]))
    watch.sort(key=lambda w: w["symbol_id"])

    # ── theme concentration, per currency ─────────────────────────────────
    # Membership weight multiplies the position weight: a symbol that is only
    # 'related 0.3' to a theme is not 100% of that theme's exposure. Themes
    # overlap, so a currency's percentages do not sum to its invested share.
    conc: dict[str, dict[str, float]] = {}
    for h in held:
        if not h["weight_pct"]:
            continue
        for t in theme_store.get(h["symbol_id"]):
            bucket = conc.setdefault(h["currency"], {})
            bucket[t["theme_id"]] = bucket.get(t["theme_id"], 0.0) + \
                h["weight_pct"] * t["weight"]
    concentration = {
        ccy: {k: round(v, 1) for k, v in
              sorted(m.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_THEMES]}
        for ccy, m in sorted(conc.items())}

    # ── plans and recent trades ───────────────────────────────────────────
    plans = []
    for p in plan_rows:
        try:
            cond = json.loads(p["conditions"]) if p.get("conditions") else None
        except (TypeError, ValueError):
            cond = None
        plans.append({
            "plan_id": p["plan_id"], "source": p.get("source"),
            "symbol_id": p["symbol_id"], "name": p.get("name"),
            "side": p.get("side"), "qty": _num(p.get("qty")),
            "price": _num(p.get("price")), "conditions": cond,
            "note": p.get("note"), "status": p.get("status"),
            "age_days": _age_days(_epoch_to_date(p.get("created_at")), today_d),
            "expires": _epoch_to_date(p.get("expires_at")),
        })

    since = today_d - timedelta(days=RECENT_TRADE_DAYS)
    cutoff = int(datetime(since.year, since.month, since.day,
                          tzinfo=KST).timestamp())
    recent_trades = [{
        "traded_at": _epoch_to_date(t["traded_at"]), "side": t["side"],
        "symbol_id": t["symbol_id"], "qty": _num(t["qty"]),
        "price": _num(t["price"]),
    } for t in _rows(
        conn,
        "SELECT traded_at, side, symbol_id, qty, price FROM trades"
        " WHERE account_id = ? AND traded_at >= ?"
        " ORDER BY traded_at DESC, trade_id DESC",
        (account_id, cutoff))]

    missing.sort(key=lambda m: (m["symbol_id"], m["why"]))
    return {
        "account": {"id": account_id, "name": acc.get("name"),
                    "kind": acc.get("kind"), "base_currency": base_ccy,
                    "date": day, "note": acc.get("note")},
        "totals": {c: totals[c] for c in sorted(totals)},
        "holdings": held,
        "watch": watch,
        "plans": plans,
        "concentration": concentration,
        "recent_trades": recent_trades,
        "missing": missing,
    }


def as_json(ctx: dict) -> str:
    """The exact bytes the model is handed.

    Unindented on purpose: pretty-printing a 19-holding account costs 30% more
    characters and carries no information the model does not already get from
    the braces (measured 82,773 → 63,854 on the auto book).
    """
    return json.dumps(ctx, ensure_ascii=False)
