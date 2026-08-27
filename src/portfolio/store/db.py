"""SQLite for portfolio-dashboard.

Relational data ONLY:
  - accounts                user-defined accounts (계좌)
  - holdings                (account_id, symbol_id) with avg_cost, qty
  - balances                (account_id, currency) cash
  - snapshots               (date, account_id, currency) daily history
  - holding_themes          (symbol_id, theme_id) qualitative theme mapping (L1)
  - chat_sessions           (session_id) per-account chat threads + transcript
  - plans                   (plan_id) per-account 매매 계획 노트
  - quant_cycles            (cycle_id) auto-trade cycle runs
  - quant_journal           (entry_id) every quant decision/order + rationale
  - quant_orders            (order_id) orders submitted to KIS, idem-keyed
  - quant_watch             (account_id, symbol_id) quant watchlist

Symbol master lives in data/universe/*.json (not in SQLite — too volatile,
loaded into memory by src/universe).
Market data lives in data/market/* (parquet, json).
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

CREATE TABLE IF NOT EXISTS holding_themes (
  symbol_id   TEXT NOT NULL,             -- 'KR:247540' (per-symbol, account-agnostic)
  theme_id    TEXT NOT NULL,             -- 'battery' (see src/agents/news/themes.json)
  relation    TEXT NOT NULL,             -- 'primary' | 'related'
  weight      REAL NOT NULL DEFAULT 1.0, -- 0.0~1.0 evaluation weight
  source      TEXT NOT NULL DEFAULT 'auto', -- 'auto' | 'manual'
  updated_at  TEXT NOT NULL,
  PRIMARY KEY (symbol_id, theme_id)
);
CREATE INDEX IF NOT EXISTS idx_holding_themes_theme ON holding_themes(theme_id);

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
  status      TEXT,                     -- NULL=manual note | armed|entered|done
                                        -- |stopped|abandoned|expired (quant lifecycle)
  conditions  TEXT,                     -- JSON {entry, take_profit, stop} (quant)
  expires_at  INTEGER,                  -- unix; past due -> expired (quant)
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_plans_account ON plans(account_id, created_at DESC);
-- idx_plans_status lives in _migrate(): it references a migrated column, and
-- SCHEMA runs before _migrate on existing DBs.

-- quant: the auto-trading plugin. One account opts in via accounts.auto_trade.
CREATE TABLE IF NOT EXISTS quant_cycles (
  cycle_id         INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id       TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
  slot             TEXT NOT NULL,          -- 'morning' | 'evening'
  focus            TEXT,                   -- 'KR' | 'US' — the market this run leads with
  main_session_id  TEXT,                   -- claude CLI session id (replayable)
  fork_a_session   TEXT,
  fork_b_session   TEXT,
  phase            TEXT NOT NULL,          -- data|brief|explore|allocate|execute|done
  status           TEXT NOT NULL,          -- running|ok|failed|skipped
  dry_run          INTEGER NOT NULL DEFAULT 0,
  started_at       INTEGER NOT NULL,
  ended_at         INTEGER
);

CREATE TABLE IF NOT EXISTS quant_journal (
  entry_id    INTEGER PRIMARY KEY AUTOINCREMENT,
  cycle_id    INTEGER REFERENCES quant_cycles(cycle_id) ON DELETE CASCADE,
  account_id  TEXT NOT NULL,
  symbol_id   TEXT,                        -- NULL = not about one symbol (brief etc.)
  kind        TEXT NOT NULL,               -- brief|candidate|assessment|decision
                                           -- |order|fill|reconcile|watch|error
  payload     TEXT NOT NULL,               -- JSON
  rationale   TEXT,                        -- human-readable reasoning
  created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_qjournal_cycle  ON quant_journal(cycle_id);
CREATE INDEX IF NOT EXISTS idx_qjournal_symbol ON quant_journal(symbol_id);

CREATE TABLE IF NOT EXISTS quant_orders (
  order_id      INTEGER PRIMARY KEY AUTOINCREMENT,
  cycle_id      INTEGER REFERENCES quant_cycles(cycle_id),
  account_id    TEXT NOT NULL,
  symbol_id     TEXT NOT NULL,
  side          TEXT NOT NULL,             -- buy | sell
  order_type    TEXT NOT NULL,             -- reserve | limit | market
  qty           REAL NOT NULL,
  price         REAL,
  idem_key      TEXT NOT NULL UNIQUE,      -- sha256(cycle|symbol|side|qty|price)
  kis_order_no  TEXT,                      -- order no / reservation seq
  status        TEXT NOT NULL,             -- dry_run|pending|submitted|filled
                                           -- |partial|cancelled|rejected|expired
  filled_qty    REAL DEFAULT 0,
  filled_price  REAL,
  cancel_if     TEXT,                      -- cancellation condition, human-readable
  rationale     TEXT,
  submitted_at  INTEGER,
  updated_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_qorders_status ON quant_orders(status);

CREATE TABLE IF NOT EXISTS quant_watch (
  account_id    TEXT NOT NULL,
  symbol_id     TEXT NOT NULL,
  reason        TEXT NOT NULL,
  target_price  REAL,
  added_cycle   INTEGER,
  updated_at    INTEGER NOT NULL,
  PRIMARY KEY (account_id, symbol_id)
);
"""


_initialized = False


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after the initial schema."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(accounts)").fetchall()}
    if "pin_hash" not in cols:
        conn.execute("ALTER TABLE accounts ADD COLUMN pin_hash TEXT")
        conn.commit()
    if "auto_trade" not in cols:
        conn.execute(
            "ALTER TABLE accounts ADD COLUMN auto_trade INTEGER NOT NULL DEFAULT 0")
        conn.commit()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(holdings)").fetchall()}
    if "name" not in cols:
        conn.execute("ALTER TABLE holdings ADD COLUMN name TEXT")
        conn.commit()
    cols = {r[1] for r in conn.execute("PRAGMA table_info(plans)").fetchall()}
    for col, decl in (("status", "TEXT"), ("conditions", "TEXT"),
                      ("expires_at", "INTEGER")):
        if col not in cols:
            conn.execute(f"ALTER TABLE plans ADD COLUMN {col} {decl}")
            conn.commit()
    conn.execute("CREATE INDEX IF NOT EXISTS idx_plans_status "
                 "ON plans(account_id, status)")
    conn.commit()


def _init_once() -> None:
    global _initialized
    if _initialized:
        return
    ensure_dirs()
    with sqlite3.connect(DB_PATH) as conn:
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
