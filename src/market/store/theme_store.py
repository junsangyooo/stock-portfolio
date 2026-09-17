"""Symbol → theme membership — data/market.db, table `symbol_themes`.

Theme membership is a property of the symbol, not of an account: two books
hold the same stock and it is in the same themes for both. It used to live in
portfolio.db as `holding_themes`, which meant quant.db's symbols had to be
read out of the other file and spliced into an IN clause to be seen at all.

Written by src/research/news/mapping.py (ETF name rule / one LLM tagging call),
read by the news agents, the account page and the quant brief.
"""
from __future__ import annotations

import time
from typing import Optional

from .db import connect


def get(symbol_id: str) -> list[dict]:
    """This symbol's themes, primary first then heaviest."""
    with connect() as c:
        rows = c.execute(
            "SELECT theme_id, relation, weight FROM symbol_themes "
            "WHERE symbol_id = ? ORDER BY relation, weight DESC",
            (symbol_id,)).fetchall()
    return [{"theme_id": r["theme_id"], "relation": r["relation"],
             "weight": float(r["weight"])} for r in rows]


def symbols_for(theme_id: str) -> list[dict]:
    """Reverse lookup — everyone mapped into this theme."""
    with connect() as c:
        rows = c.execute(
            "SELECT symbol_id, relation, weight FROM symbol_themes "
            "WHERE theme_id = ? ORDER BY relation, symbol_id",
            (theme_id,)).fetchall()
    return [{"symbol_id": r["symbol_id"], "relation": r["relation"],
             "weight": float(r["weight"])} for r in rows]


def has(symbol_id: str) -> bool:
    """True if already mapped — the guard that stops a re-add from burning
    another LLM tagging call."""
    with connect() as c:
        return c.execute("SELECT 1 FROM symbol_themes WHERE symbol_id = ? "
                         "LIMIT 1", (symbol_id,)).fetchone() is not None


def save(symbol_id: str, rows: list[dict],
         updated_at: Optional[str] = None) -> None:
    """Replace this symbol's mapping (idempotent: delete + insert)."""
    ts = updated_at or time.strftime("%Y-%m-%dT%H:%M:%S")
    with connect() as c:
        c.execute("DELETE FROM symbol_themes WHERE symbol_id = ?", (symbol_id,))
        c.executemany(
            "INSERT INTO symbol_themes "
            "(symbol_id, theme_id, relation, weight, source, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            [(symbol_id, r["theme_id"], r["relation"], r["weight"],
              r.get("source", "auto"), ts) for r in rows])
        c.commit()
