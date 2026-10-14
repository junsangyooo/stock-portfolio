"""The account ledger — what an account owns, what it paid, what it did.

portfolio.db applies it and adds its own `chat_sessions` on top
(src/portfolio/store/db.py).

A person keeps no plans — what to do about a holding is read off its analysis
on the card. The old automatic account's tables (`plans`, `watch`) and columns
(`accounts.kind`/`mode`, `holdings.entry_plan_id`/`entry_rationale`,
`trades.plan_id`) were dropped on 2026-10-14, all of them empty.
"""
from __future__ import annotations

import sqlite3

TABLES = """
-- One row per account.
CREATE TABLE IF NOT EXISTS accounts (
  account_id     TEXT PRIMARY KEY,
  name           TEXT NOT NULL,
  account_type   TEXT,                  -- ISA | GENERAL | PENSION | OVERSEAS
  broker         TEXT,
  base_currency  TEXT NOT NULL DEFAULT 'KRW',
  pin_hash       TEXT,                  -- secret-salted sha256 of a 6-digit PIN
  sort_order     INTEGER NOT NULL DEFAULT 0,
  note           TEXT,
  created_at     INTEGER NOT NULL
);

-- What the account owns right now. Not derived from `trades`: a person holds
-- things they never recorded a trade for. Current state and history answer
-- different questions.
--
-- opened_at is written once when the position opens and left alone after
-- (COALESCE).
CREATE TABLE IF NOT EXISTS holdings (
  account_id       TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
  symbol_id        TEXT NOT NULL,       -- 'KR:005930' | 'US:AAPL'
  name             TEXT,                -- display override; NULL = universe name
  qty              REAL NOT NULL DEFAULT 0,
  avg_cost         REAL,                -- in `currency`, not base
  currency         TEXT,
  note             TEXT,
  opened_at        INTEGER,
  added_at         INTEGER NOT NULL,
  sort_order       INTEGER NOT NULL DEFAULT 0,
  updated_at       INTEGER NOT NULL,
  PRIMARY KEY (account_id, symbol_id)
);

-- What actually happened. A fill, not a submission: a person trades in their
-- broker's app and records the result, so the columns an order needs (idem
-- key, broker order id, pending→filled) would sit empty forever.
CREATE TABLE IF NOT EXISTS trades (
  trade_id    INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id  TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
  symbol_id   TEXT NOT NULL,
  name        TEXT,                     -- name at trade time; survives a
                                        -- universe drop
  side        TEXT NOT NULL,            -- 'buy' | 'sell'
  qty         REAL NOT NULL,
  price       REAL NOT NULL,
  fee         REAL NOT NULL DEFAULT 0,
  currency    TEXT,
  traded_at   INTEGER NOT NULL,         -- when it happened, not when recorded
  source      TEXT NOT NULL DEFAULT 'manual',  -- manual | auto | import
  note        TEXT,
  created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS cash (
  account_id  TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
  currency    TEXT NOT NULL,
  amount      REAL NOT NULL DEFAULT 0,
  updated_at  INTEGER NOT NULL,
  PRIMARY KEY (account_id, currency)
);

CREATE TABLE IF NOT EXISTS snapshots (
  date          TEXT NOT NULL,          -- 'YYYY-MM-DD' KST
  account_id    TEXT NOT NULL,
  currency      TEXT NOT NULL,
  eval          REAL NOT NULL DEFAULT 0,
  cost          REAL NOT NULL DEFAULT 0,
  pnl           REAL NOT NULL DEFAULT 0,
  cash          REAL NOT NULL DEFAULT 0,
  total_assets  REAL NOT NULL DEFAULT 0,
  PRIMARY KEY (date, account_id, currency)
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_holdings_symbol ON holdings(symbol_id);
CREATE INDEX IF NOT EXISTS idx_trades_account ON trades(account_id, traded_at DESC);
CREATE INDEX IF NOT EXISTS idx_trades_symbol  ON trades(account_id, symbol_id, traded_at DESC);
CREATE INDEX IF NOT EXISTS idx_snapshots_date    ON snapshots(date);
CREATE INDEX IF NOT EXISTS idx_snapshots_account ON snapshots(account_id);
"""

def apply(conn: sqlite3.Connection) -> None:
    """Create the ledger tables, then index them."""
    conn.executescript(TABLES)
    conn.executescript(INDEXES)
    conn.commit()
