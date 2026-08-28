"""SQLite for portfolio-dashboard.

Relational data ONLY:
  - accounts                user-defined accounts (계좌)
  - holdings                (account_id, symbol_id) with avg_cost, qty
  - balances                (account_id, currency) cash
  - snapshots               (date, account_id, currency) daily history
  - chat_sessions           (session_id) per-account chat threads + transcript
  - plans                   (plan_id) per-account 매매 계획 노트

The auto-trading book is NOT here — it has its own file, data/quant.db
(src/quant/db.py). The two share their sources, never their books.

Symbol master lives in data/universe/*.json (not in SQLite — too volatile,
loaded into memory by src/universe).
Everything true about a *symbol* — bars, statements, valuation, sector,
ETF facts, theme membership — lives in data/market.db (src/market/store/db.py),
shared with the quant book. Theme mapping was `holding_themes` here until
2026-08-28; it never was account data.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from ...core.paths import DB_PATH, ensure_dirs

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
  account_id    TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  account_type  TEXT NOT NULL,            -- 'ISA' | 'GENERAL' | 'PENSION' | 'OVERSEAS' | 'OTHER'
  broker        TEXT,
  base_currency TEXT NOT NULL DEFAULT 'KRW',
  sort_order    INTEGER NOT NULL,
  note          TEXT,
  created_at    INTEGER NOT NULL,
  pin_hash      TEXT                       -- secret-salted sha256 of 6-digit PIN (NULL = no PIN set)
);

CREATE TABLE IF NOT EXISTS holdings (
  account_id    TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
  symbol_id     TEXT NOT NULL,            -- 'KR:005930' | 'US:AAPL'
  avg_cost      REAL,
  qty           REAL,
  note          TEXT,
  name          TEXT,                     -- display-name override (NULL = use the universe name)
  added_at      INTEGER NOT NULL,
  sort_order    INTEGER NOT NULL,
  PRIMARY KEY (account_id, symbol_id)
);
CREATE INDEX IF NOT EXISTS idx_holdings_symbol ON holdings(symbol_id);

CREATE TABLE IF NOT EXISTS balances (
  account_id    TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
  currency      TEXT NOT NULL,
  amount        REAL NOT NULL DEFAULT 0,
  updated_at    INTEGER NOT NULL,
  PRIMARY KEY (account_id, currency)
);

CREATE TABLE IF NOT EXISTS snapshots (
  date          TEXT NOT NULL,            -- 'YYYY-MM-DD' KST
  account_id    TEXT NOT NULL,
  currency      TEXT NOT NULL,
  eval          REAL NOT NULL DEFAULT 0,
  cost          REAL NOT NULL DEFAULT 0,
  pnl           REAL NOT NULL DEFAULT 0,
  cash          REAL NOT NULL DEFAULT 0,
  total_assets  REAL NOT NULL DEFAULT 0,
  PRIMARY KEY (date, account_id, currency)
);
CREATE INDEX IF NOT EXISTS idx_snapshots_date ON snapshots(date);
CREATE INDEX IF NOT EXISTS idx_snapshots_account ON snapshots(account_id);

CREATE TABLE IF NOT EXISTS chat_sessions (
  session_id  TEXT PRIMARY KEY,           -- uuid4, doubles as the claude CLI --session-id
  account_id  TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
  title       TEXT NOT NULL,              -- first user question, trimmed
  messages    TEXT NOT NULL DEFAULT '[]', -- JSON [{role, content}] for replaying the thread
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_acc ON chat_sessions(account_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS plans (
  plan_id     INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id  TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
  symbol_id   TEXT NOT NULL,            -- 'KR:012450' — a plan may name a symbol
                                        -- the account does not hold yet
  name        TEXT NOT NULL,            -- name at write time; the note stays
                                        -- readable even if universe drops it
  side        TEXT NOT NULL,            -- 'buy' | 'sell'
  price       REAL NOT NULL,
  qty         REAL NOT NULL,
  note        TEXT,
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plans_account ON plans(account_id, created_at DESC);
"""


_initialized = False


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after the initial schema."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(accounts)").fetchall()}
    if "pin_hash" not in cols:
        conn.execute("ALTER TABLE accounts ADD COLUMN pin_hash TEXT")
        conn.commit()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(holdings)").fetchall()}
    if "name" not in cols:
        conn.execute("ALTER TABLE holdings ADD COLUMN name TEXT")
        conn.commit()
    conn.commit()


def _init_once() -> None:
    global _initialized
    if _initialized:
        return
    ensure_dirs()
    with sqlite3.connect(DB_PATH) as conn:
        # WAL so the cron writers (snapshot_job, news daily_job) never block
        # the dashboard's reads. A property of the file, set once.
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.commit()
    _initialized = True


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    _init_once()
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()
