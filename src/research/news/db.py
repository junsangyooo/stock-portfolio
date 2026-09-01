"""SQLite for the qualitative pipeline — data/news.db.

The fourth book, and the only one about the outside world. The three others
answer "what do I own" (portfolio.db, quant.db) and "what is true about this
symbol" (market.db); this one answers "what happened, when, in which field".

It replaces data/signals/news/{theme}/{yyyymm}.jsonl, which stored one copy of
an article per theme it belonged to and could only be searched by reading a
month at a time. Two indexes here are the whole point: an article is one row
with N theme rows beside it, and (date, theme) is a lookup rather than a scan.

Three time layers, written by three cycles and never read backwards:

    signals + theme_daily     every collection run (2x/day)
    theme_weekly              Sunday evening, reading theme_daily
    theme_monthly             last Sunday of the month, reading theme_weekly

One file, not three, because the weekly cycle joins the daily layer and a
backup should not have to catch three files at the same instant.

WAL: the collection cron writes while the dashboard reads the landing feed.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from ...core import paths

SCHEMA = """
-- One row per article, whatever it was collected for. `dedup_key` is the url
-- when there is one and a hash of the title when there is not — the same rule
-- the jsonl store used, so a re-run of a cycle is a no-op rather than a
-- second copy.
CREATE TABLE IF NOT EXISTS signals (
  signal_id     INTEGER PRIMARY KEY AUTOINCREMENT,
  dedup_key     TEXT NOT NULL UNIQUE,
  url           TEXT,
  title         TEXT NOT NULL,
  body          TEXT,                     -- the source's own summary/abstract
  source_id     TEXT NOT NULL,
  source_group  TEXT NOT NULL,            -- research | social | news
  lead_lag      TEXT NOT NULL,            -- leading | coincident | lagging
                                          -- (from source_group; the old
                                          -- "backbone" is now just macro
                                          -- theme membership)
  image         TEXT,
  published_at  TEXT,                     -- ISO, as the source reported it
  fetched_at    TEXT NOT NULL,            -- ISO KST, when we pulled it
  collected_on  TEXT NOT NULL,            -- 'YYYY-MM-DD' KST — the date axis
  -- filled by the enrichment pass, in the same call that assigns themes
  summary       TEXT,
  importance    INTEGER,                  -- 1~5
  novelty       INTEGER,                  -- 0 | 1
  enriched_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_signals_day ON signals(collected_on);
-- "the day's items worth reading" without sorting the day
CREATE INDEX IF NOT EXISTS idx_signals_day_imp
  ON signals(collected_on, importance DESC);
-- the enrichment pass's work queue
CREATE INDEX IF NOT EXISTS idx_signals_unenriched
  ON signals(collected_on) WHERE enriched_at IS NULL;

-- An article can belong to several fields; the old layout expressed that by
-- storing it several times.
CREATE TABLE IF NOT EXISTS signal_themes (
  signal_id   INTEGER NOT NULL REFERENCES signals(signal_id) ON DELETE CASCADE,
  theme_id    TEXT NOT NULL,
  confidence  REAL NOT NULL DEFAULT 1.0,
  PRIMARY KEY (signal_id, theme_id)
);
CREATE INDEX IF NOT EXISTS idx_signal_themes_theme ON signal_themes(theme_id);

-- What happened in one field on one day. The landing page's 「오늘의 요약」
-- reads `overview` directly.
--
-- sentiment and watchlist ride on the same call that writes the bullets: they
-- are the same reading, and the judge and the quant brief need a number and a
-- list of open issues rather than prose. They used to come from a rolling
-- state file with its own LLM rewrite; folding them in here costs no extra
-- inference and leaves exactly one long memory (weekly/monthly) instead of two
-- that can disagree.
CREATE TABLE IF NOT EXISTS theme_daily (
  theme_id         TEXT NOT NULL,
  date             TEXT NOT NULL,         -- 'YYYY-MM-DD' KST
  overview         TEXT NOT NULL,         -- JSON ["bullet", ...]
  sentiment_score  REAL,                  -- -1 ~ 1
  sentiment_trend  TEXT,                  -- improving | flat | deteriorating
  watchlist        TEXT,                  -- JSON ["open issue", ...]
  n_items          INTEGER NOT NULL DEFAULT 0,
  built_at         TEXT NOT NULL,
  PRIMARY KEY (theme_id, date)
);

-- Sunday evening: that week's theme_daily rows, one call per theme.
CREATE TABLE IF NOT EXISTS theme_weekly (
  theme_id    TEXT NOT NULL,
  week_start  TEXT NOT NULL,              -- 'YYYY-MM-DD', the Monday, KST
  summary     TEXT NOT NULL,
  bullets     TEXT,                       -- JSON
  n_days      INTEGER NOT NULL DEFAULT 0,
  built_at    TEXT NOT NULL,
  PRIMARY KEY (theme_id, week_start)
);

-- Last Sunday of the month, right after the weekly run: that month's
-- theme_weekly rows.
CREATE TABLE IF NOT EXISTS theme_monthly (
  theme_id   TEXT NOT NULL,
  month      TEXT NOT NULL,               -- 'YYYY-MM'
  summary    TEXT NOT NULL,
  bullets    TEXT,                        -- JSON
  n_weeks    INTEGER NOT NULL DEFAULT 0,
  built_at   TEXT NOT NULL,
  PRIMARY KEY (theme_id, month)
);

-- "everything since the last cycle" — the alternative is re-reading each feed
-- from the top and leaning on dedup, which works but grows the fetch forever.
-- The failure columns are here so a feed that has been dead for a week is
-- visible instead of merely quiet (§10.1: source failures leave a trace).
CREATE TABLE IF NOT EXISTS source_cursor (
  source_id           TEXT PRIMARY KEY,
  last_fetched_at     TEXT,               -- ISO of the newest item we accepted
  last_ok_at          TEXT,               -- ISO of the last successful fetch
  last_error          TEXT,
  n_consecutive_fail  INTEGER NOT NULL DEFAULT 0
);
"""

# Keyed by path so a test pointing NEWS_DB_PATH at a tmp file still gets a
# schema (see src/market/store/db.py for the same reasoning).
_initialized: set = set()


def _init_once(conn: sqlite3.Connection, path) -> None:
    if path in _initialized:
        return
    conn.executescript(SCHEMA)
    conn.commit()
    _initialized.add(path)


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = paths.NEWS_DB_PATH
    paths.ensure_dirs()
    conn = sqlite3.connect(path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")   # signal_themes CASCADE
        _init_once(conn, path)
        yield conn
    finally:
        conn.close()
