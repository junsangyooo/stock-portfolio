"""SQLite for the human accounts — data/portfolio.db.

The ledger itself (accounts, holdings, trades, cash, snapshots, plans, watch)
is defined once in src/ledger and applied here; this file only adds what is
specific to this book. The human book keeps no plans — the `plans` table
exists because the ledger is one definition, and stays empty here.

  chat_sessions   per-account chat threads + their transcript

The auto-trading book is NOT here — it has its own file, data/quant.db
(src/quant/db.py), built from the same ledger. Ownership is the axis: a
migration on one cannot touch the other, and quant's journal does not share a
file with a static human record set.

Symbol master lives in data/universe/*.json (too volatile for SQLite, loaded
into memory by src/universe). Everything true about a *symbol* — bars,
statements, valuation, sector, ETF facts, theme membership — lives in
data/market.db, shared with the quant book.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from ...core.paths import DB_PATH, ensure_dirs
from ...ledger import apply as apply_ledger

# This book's own tables, on top of the shared ledger.
EXTENSION = """
CREATE TABLE IF NOT EXISTS chat_sessions (
  session_id  TEXT PRIMARY KEY,           -- uuid4, doubles as the claude CLI
                                          -- --session-id
  account_id  TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
  title       TEXT NOT NULL,              -- first user question, trimmed
  messages    TEXT NOT NULL DEFAULT '[]', -- JSON [{role, content}], for
                                          -- replaying the thread on screen
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_acc
  ON chat_sessions(account_id, updated_at DESC);
"""

_initialized = False


def _init_once(conn: sqlite3.Connection) -> None:
    global _initialized
    if _initialized:
        return
    apply_ledger(conn)
    conn.executescript(EXTENSION)
    conn.commit()
    _initialized = True


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        # WAL so the cron writers (snapshot_job, news cycle) never block a read
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _init_once(conn)
        yield conn
    finally:
        conn.close()
