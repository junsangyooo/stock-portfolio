"""Which stored analyses are worth redoing today.

The old rule was "every symbol, every day", so the bill grew with the
watchlist rather than with anything happening in the market — 32 symbols in
early September, 59 ten days later, same market. This asks the other
question: what has changed since the stored analysis was written?

Five changes count, and they are different kinds of change on purpose — a
price crossing a coordinate since the analysis (its premise broke), a new filing (half the
inputs are different), a high-importance article naming the company, a move
large enough to matter without touching a coordinate, and age as the backstop.
All of them are read from what is already stored; nothing here collects.

Account-free by construction (ops notes §4). The caller passes its symbols;
this module never opens a book.

    from src.research import analyze
    analyze.due_among(symbols)          # the shortlist to run
    analyze.due_detail(symbols)         # …with the reason
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Iterable, Optional

from ...core import kst
from .read import latest

MAX_AGE = 7               # trading days — the backstop when nothing else fired

MOVE_PCT = 5.0            # move from the analysed price that counts on its own
NEWS_DAYS = 14            # how far back a naming article still counts
NEWS_IMPORTANCE = 4       # 1~5; 4 is "moves the investment case"
DAILY_LIMIT = 20          # ceiling per run, highest-priority first

# Priority order when the limit bites. A broken premise outranks a number
# going up, and age outranks nothing.
_REASONS = ("missing", "facts_only", "level", "filing", "news", "move", "age")


def _as_date(s) -> Optional[date]:
    try:
        return datetime.strptime(str(s), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _weekdays_since(day: date, today: date) -> int:
    """Trading days, approximated by weekdays.

    The exchange calendar lives behind the broker, which is an app, and this
    layer may not read one. A holiday therefore makes a symbol come up one day
    early — never one day late, which is the direction that would matter.
    """
    n, cur = 0, day
    while cur < today:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            n += 1
    return n


def _crossed(price, was: float, close: float) -> bool:
    """`close` is on the other side of `price` from where the analysis stood,
    or on it. A coordinate the analysis already stood past is its premise —
    a sell verdict's invalidation sits above the price — not a change."""
    return bool(price) and was != price and (was - price) * (close - price) <= 0


def _level_hit(levels: dict, was: Optional[float], close: float) -> Optional[str]:
    """Which stored coordinate the price has crossed since the analysis, if
    any. Without the analysed price there is no "since" — age covers it."""
    if not was:
        return None
    inv = (levels.get("invalidation") or {}).get("price")
    if _crossed(inv, was, close):
        return f"손절선 {inv:,.0f} 통과"
    for t in levels.get("targets") or []:
        px = t.get("price") if isinstance(t, dict) else None
        if _crossed(px, was, close):
            return f"목표 {px:,.0f} 통과"
    ez = levels.get("entry_zone") or {}
    lo, hi = ez.get("low"), ez.get("high")
    if lo and hi and lo <= close <= hi and not lo <= was <= hi:
        return f"진입구간 {lo:,.0f}~{hi:,.0f} 진입"
    return None


def _closes(ids: list[str]) -> dict:
    from ...market.store import ohlcv_store
    try:
        return ohlcv_store.read_closes(ids, 1)
    except Exception:  # noqa: BLE001 — a missing price is not a reason to skip
        return {}


def _filings(ids: list[str]) -> dict:
    from ...market.store import financials_store
    try:
        return financials_store.latest_filed_at(ids)
    except Exception:  # noqa: BLE001
        return {}


def _mentions(ids: list[str]) -> dict:
    from ..news import reverse
    try:
        return reverse.recent_mentions(ids, days=NEWS_DAYS,
                                       min_importance=NEWS_IMPORTANCE)
    except Exception:  # noqa: BLE001
        return {}


def due_detail(symbol_ids: Iterable[str], *, limit: int = DAILY_LIMIT,
               now: Optional[datetime] = None) -> list[dict]:
    """[{symbol_id, reason, detail, age_days}] for the symbols worth redoing,
    most urgent first, cut at `limit` (0 = no cut)."""
    today = (now or kst.now()).date()
    ids = list(dict.fromkeys(symbol_ids))
    if not ids:
        return []
    closes, filed, mentioned = _closes(ids), _filings(ids), _mentions(ids)

    out: list[dict] = []
    for sid in ids:
        body = latest(sid)
        if body is None:
            out.append({"symbol_id": sid, "reason": "missing",
                        "detail": "저장된 분석 없음", "age_days": None})
            continue
        if body.get("status") != "ok":
            # A `facts_only` result is a failed call with the calculated
            # numbers kept — it has no judgment and no coordinates, so no
            # other trigger here can ever fire on it. Without this it would
            # sit unjudged until it aged out: 49 of 81 symbols were in that
            # state after the weekly limit ran out on 2026-09-15.
            out.append({"symbol_id": sid, "reason": "facts_only",
                        "detail": f"판정 없는 결과({body.get('status')})",
                        "age_days": None})
            continue
        day = _as_date(body.get("date"))
        age = _weekdays_since(day, today) if day else None
        stored = day.isoformat() if day else ""
        close = (closes.get(sid) or [None])[-1]
        levels = body.get("levels") or {}
        was = levels.get("current")

        hit = _level_hit(levels, was, close) if close else None
        filing = filed.get(sid)
        article = mentioned.get(sid)
        if hit:
            reason, detail = "level", f"현재가 {close:,.0f} — {hit}"
        elif stored and filing and filing[:10] > stored:
            reason, detail = "filing", f"새 공시 {filing[:10]}"
        elif stored and article and article > stored:
            reason, detail = "news", f"중요 기사 {article}"
        elif close and was and abs(close / was - 1) * 100 >= MOVE_PCT:
            reason = "move"
            detail = f"분석 시점 대비 {(close / was - 1) * 100:+.1f}%"
        elif age is None or age > MAX_AGE:
            reason, detail = "age", f"{age}거래일 경과 (상한 {MAX_AGE})"
        else:
            continue
        out.append({"symbol_id": sid, "reason": reason, "detail": detail,
                    "age_days": age})

    out.sort(key=lambda r: (_REASONS.index(r["reason"]),
                            -(r["age_days"] if r["age_days"] is not None else 999)))
    return out[:limit] if limit else out


def due_among(symbol_ids: Iterable[str], *, limit: int = DAILY_LIMIT,
              now: Optional[datetime] = None) -> list[str]:
    """Just the shortlist — what a caller hands to batch_job."""
    return [r["symbol_id"] for r in
            due_detail(symbol_ids, limit=limit, now=now)]
