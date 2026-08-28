"""SQLite for the symbol book — data/market.db.

The third book. portfolio.db holds one person's accounts, quant.db holds the
auto-trading account, and this holds everything that is true about a *symbol*
regardless of who owns it: bars, statements, valuation, sector, ETF facts and
theme membership. Both account books read it; neither owns it.

Why one file and not kr.db + us.db: `symbol_id` has been market-agnostic since
2026-05-23 (`KR:` | `US:`), and the queries that matter cross markets — a
total-assets roll-up, "which symbols are in this theme", a cross-sectional
screen. Splitting by market would push a market branch back into every read
path for no gain: the schema, the growth profile and the blast radius are the
same on both sides. Only the *source* differs, and that is a pipeline concern.

Not here:
  - the symbol master. data/universe/{kr,us}.json already is it (§10.1: daily
    batch, searched in memory). A copy in SQLite would be a second truth.
  - live quotes. quote_store stays an in-memory dict with a JSON flush; it is
    written on every poll and would only buy disk writes here.

Both other books are WAL and so is this one — cron pipelines write it while
the dashboard reads.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from ...core import paths

SCHEMA = """
-- Daily bars only. Weekly/monthly are resampled on read (pipeline/resampler).
-- WITHOUT ROWID: the primary key IS the table, so one symbol's bars sit
-- contiguously — the read pattern that matters ("last N bars of X") is a
-- range scan, and at ~8M rows the saved rowid index is worth the clause.
CREATE TABLE IF NOT EXISTS ohlcv (
  symbol_id  TEXT NOT NULL,
  ts         INTEGER NOT NULL,          -- unix seconds at bar open
  open       REAL, high REAL, low REAL, close REAL, volume REAL,
  PRIMARY KEY (symbol_id, ts)
) WITHOUT ROWID;
-- cross-sectional: every symbol on one date (screening, backtests)
CREATE INDEX IF NOT EXISTS idx_ohlcv_ts ON ohlcv(ts);

CREATE TABLE IF NOT EXISTS valuation (
  symbol_id  TEXT NOT NULL,
  ts         INTEGER NOT NULL,
  per REAL, pbr REAL, psr REAL, eps REAL, bps REAL, dps REAL,
  div_yield REAL, market_cap REAL,
  PRIMARY KEY (symbol_id, ts)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_valuation_ts ON valuation(ts);

-- One row per reporting period. A re-fetch of the same period overwrites
-- (restatements). filed_at is the disclosure receipt date — the column the
-- event-driven refresh keys off, so "is our copy older than the filing?" is
-- answerable without calling DART/EDGAR.
CREATE TABLE IF NOT EXISTS financials (
  symbol_id         TEXT NOT NULL,
  period            TEXT NOT NULL,      -- '2024Q3' | '2024FY'
  fs_div            TEXT,               -- CFS | OFS
  report_date       TEXT,
  source            TEXT,               -- dart | yfinance | sec
  filed_at          TEXT,               -- ISO date of the disclosure itself
  revenue REAL, gross_profit REAL, operating_income REAL, net_income REAL,
  eps REAL, total_assets REAL, total_liabilities REAL, total_equity REAL,
  current_assets REAL, current_liabilities REAL,
  operating_cf REAL, investing_cf REAL, financing_cf REAL,
  PRIMARY KEY (symbol_id, period)
);

CREATE TABLE IF NOT EXISTS sector (
  symbol_id   TEXT PRIMARY KEY,
  sector      TEXT,
  industry    TEXT,
  source      TEXT,
  updated_at  TEXT NOT NULL
);

-- Latest snapshot per ETF, overwritten. History lives in ohlcv.
-- `snapshot` is the source's own shape (naver_etf_source.shape) — NAV, AUM,
-- 괴리율, category — which varies by provider and has no business becoming
-- columns nobody queries.
CREATE TABLE IF NOT EXISTS etf (
  symbol_id   TEXT PRIMARY KEY,
  snapshot    TEXT NOT NULL,            -- JSON
  updated_at  TEXT NOT NULL
);

-- Theme membership is a property of the symbol, not of an account. It lived
-- in portfolio.db as `holding_themes`, which forced quant.db's symbols to be
-- fetched from the other file and spliced into an IN clause. Here both books
-- just read it.
CREATE TABLE IF NOT EXISTS symbol_themes (
  symbol_id   TEXT NOT NULL,
  theme_id    TEXT NOT NULL,
  relation    TEXT NOT NULL,            -- 'primary' | 'related'
  weight      REAL NOT NULL DEFAULT 1.0,
  source      TEXT NOT NULL DEFAULT 'auto',
  updated_at  TEXT NOT NULL,
  PRIMARY KEY (symbol_id, theme_id)
);
CREATE INDEX IF NOT EXISTS idx_symbol_themes_theme ON symbol_themes(theme_id);
"""

# Keyed by path, not a bare bool: tests point MARKET_DB_PATH at a tmp file and
# a process-wide flag would leave the new file schemaless.
_initialized: set = set()


def _init_once(conn: sqlite3.Connection, path) -> None:
    if path in _initialized:
        return
    conn.executescript(SCHEMA)
    conn.commit()
    _initialized.add(path)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    # Read the path at call time — `paths` is import-light, so a test can
    # redirect it without dragging in the whole market package.
    path = paths.MARKET_DB_PATH
    paths.ensure_dirs()
    conn = sqlite3.connect(path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        _init_once(conn, path)
        yield conn
    finally:
        conn.close()
