"""plan_runs — what the plan writer decided, per account per day.

The screen reads this, not the plans table: a card shows "what does this
account think about this symbol today", which is a line the pass wrote, and
twenty cards must not cost twenty reads of a diff. `notes_by_symbol` is its
own column for exactly that, so `latest()` hands the grid one row.

Writing is src/ledger/planner.py — this side only reads.
"""
from __future__ import annotations

import json
from typing import List, Optional

from .db import connect


def _json(raw, default):
    try:
        return json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default


def _shape(row) -> dict:
    return {
        "date": row["date"], "slot": row["slot"], "status": row["status"],
        "portfolio_note": row["portfolio_note"] or "",
        "notes_by_symbol": _json(row["notes_by_symbol"], {}),
        "suggest": _json(row["accepted"], {}).get("suggest") or [],
        "n_rejected": len(_json(row["rejected"], [])),
        "created_at": row["created_at"],
    }


def latest(account_id: str) -> Optional[dict]:
    """The newest pass that produced something.

    A failed pass is not an answer for the card grid — the badges would empty
    out because the model timed out once. status_row() is where a failure is
    visible, and it is visible on purpose.
    """
    with connect() as c:
        row = c.execute(
            "SELECT * FROM plan_runs WHERE account_id = ? AND status = 'ok'"
            " ORDER BY date DESC, created_at DESC LIMIT 1",
            (account_id,)).fetchone()
        return _shape(row) if row else None


def on(account_id: str, date: str) -> Optional[dict]:
    """That day's pass — the morning one, or the preview if that is all there
    is. Carries the rejections in full: a person reading one day wants to know
    what the model asked for and did not get."""
    with connect() as c:
        row = c.execute(
            "SELECT * FROM plan_runs WHERE account_id = ? AND date = ?"
            " ORDER BY CASE slot WHEN 'morning' THEN 0 ELSE 1 END,"
            "          created_at DESC LIMIT 1",
            (account_id, date)).fetchone()
        if not row:
            return None
        out = _shape(row)
        out["rejected"] = _json(row["rejected"], [])
        out["accepted"] = _json(row["accepted"], {})
        return out


def dates(account_id: str, limit: int = 30) -> List[str]:
    with connect() as c:
        rows = c.execute(
            "SELECT DISTINCT date FROM plan_runs WHERE account_id = ?"
            " ORDER BY date DESC LIMIT ?", (account_id, int(limit))).fetchall()
        return [r["date"] for r in rows]


def status_row(account_id: str, date: str, slot: str) -> Optional[dict]:
    """One slot's outcome, failures included — "오늘 아침은 parse_error였다"
    is something the screen has to be able to say."""
    with connect() as c:
        row = c.execute(
            "SELECT date, slot, status, model, elapsed_s, context_chars,"
            " created_at FROM plan_runs"
            " WHERE account_id = ? AND date = ? AND slot = ?",
            (account_id, date, slot)).fetchone()
        return dict(row) if row else None
