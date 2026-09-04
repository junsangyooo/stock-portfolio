"""The account ledger — one schema, two books.

portfolio.db and quant.db hold the same thing: what an account owns, what it
paid, what it plans, what it did. They had grown two vocabularies for it —
`holdings`/`positions`, `balances`/`cash`, `accounts`/`account` — and every
feature had to be built twice, which is why the human book still had no trade
history at all.

The difference between the two was never the ledger. It is who acts on it:

    plan.status   portfolio (사람이 실행)      quant (keeper·브로커가 실행)
    armed         계획을 적어뒀다              조건주문 등록됨
    entered       샀다                         체결됐다
    done|stopped  정리했다                     익절·손절
    expired       기한이 지났다                사이클이 철회

Same transitions, and the same thing causes them: a `trade` row. quant's
execute writes one when a fill comes back; portfolio writes one when the
person records what they did. The executor differs, the mechanism does not.

Two files, not one, because ownership is the axis (ops notes §3): a migration
on one book cannot touch the other, and quant's journal does not share a file
with a static human record set. Each book adds its own tables on top —
portfolio `chat_sessions`, quant `cycles`/`journal`/`orders`.
"""
from __future__ import annotations

import sqlite3

TABLES = """
-- One row per account. quant has exactly one; portfolio has several.
CREATE TABLE IF NOT EXISTS accounts (
  account_id     TEXT PRIMARY KEY,
  name           TEXT NOT NULL,
  kind           TEXT NOT NULL DEFAULT 'human',  -- 'human' | 'auto'
  account_type   TEXT,                  -- ISA | GENERAL | PENSION | OVERSEAS
  broker         TEXT,
  base_currency  TEXT NOT NULL DEFAULT 'KRW',
  pin_hash       TEXT,                  -- secret-salted sha256 of a 6-digit PIN
  mode           INTEGER,               -- auto only: 1 off | 2 dry-run | 3 live
  paused_at      INTEGER,               -- auto only: NULL = running; set = no
                                        -- new entries, exits still guarded
  sort_order     INTEGER NOT NULL DEFAULT 0,
  note           TEXT,
  created_at     INTEGER NOT NULL
);

-- What the account owns right now. Not derived from `trades`: quant's truth is
-- the broker's reconcile, and a person holds things they never recorded a
-- trade for. Current state and history answer different questions.
--
-- entry_* is written once when the position opens and reconcile leaves it
-- alone (COALESCE) — a changed quantity does not change why it was bought.
CREATE TABLE IF NOT EXISTS holdings (
  account_id       TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
  symbol_id        TEXT NOT NULL,       -- 'KR:005930' | 'US:AAPL'
  name             TEXT,                -- display override; NULL = universe name
  qty              REAL NOT NULL DEFAULT 0,
  avg_cost         REAL,                -- in `currency`, not base
  currency         TEXT,
  note             TEXT,
  entry_plan_id    INTEGER,             -- the plan it came from
  entry_rationale  TEXT,                -- why, copied at open time
  opened_at        INTEGER,
  added_at         INTEGER NOT NULL,
  sort_order       INTEGER NOT NULL DEFAULT 0,
  updated_at       INTEGER NOT NULL,
  PRIMARY KEY (account_id, symbol_id)
);

-- What actually happened. A fill, not a submission: a person trades in their
-- broker's app and records the result, so the columns an order needs (idem
-- key, broker order id, pending→filled) would sit empty forever on that side.
-- quant keeps those in its own `orders` table and writes a trade when one fills.
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
  plan_id     INTEGER,                  -- the plan this fulfils, if any
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

-- A plan is an order ticket whoever reads it. The person's note and the
-- machine's standing order are the same row; only the executor differs.
--
-- `conditions` holds predicates a machine can evaluate. Anything qualitative
-- ("실적 발표 후") stays in `note` — for quant the next cycle re-judges it,
-- for a person it is simply something to read.
--
--   {"entry":       {"kind":"immediate"|"below"|"above"|"agent","price":N}
--    "take_profit": {"kind":"above","price":N},
--    "stop":        {"kind":"below","price":N}}
--
-- `conditional_order_id` is the single truth for who is watching a price
-- condition: the broker when set, the keeper when NULL. Only ever filled when
-- a broker is actually watching — for a human plan it stays NULL.
CREATE TABLE IF NOT EXISTS plans (
  plan_id               INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id            TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
  symbol_id             TEXT NOT NULL,  -- may name a symbol not held yet
  name                  TEXT NOT NULL,
  side                  TEXT NOT NULL,  -- 'buy' | 'sell'
  price                 REAL NOT NULL,  -- reference price at write time
  qty                   REAL NOT NULL,
  note                  TEXT,
  status                TEXT NOT NULL DEFAULT 'armed',
                        -- armed|entered|done|stopped|abandoned|expired
  source                TEXT NOT NULL DEFAULT 'agent',  -- user | agent
  conditions            TEXT,           -- JSON, see above
  conditional_order_id  TEXT,
  expires_at            INTEGER,
  cycle_id              INTEGER,        -- auto only: the cycle that wrote it;
                                        -- NULL for a person's or a user's plan
  created_at            INTEGER NOT NULL,
  updated_at            INTEGER NOT NULL
);

-- Symbols the account is interested in but does not own.
CREATE TABLE IF NOT EXISTS watch (
  account_id    TEXT NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
  symbol_id     TEXT NOT NULL,
  name          TEXT,
  reason        TEXT NOT NULL,
  target_price  REAL,
  added_cycle   INTEGER,                -- auto only: which cycle added it
  updated_at    INTEGER NOT NULL,
  PRIMARY KEY (account_id, symbol_id)
);
"""

INDEXES = """\nCREATE INDEX IF NOT EXISTS idx_holdings_symbol ON holdings(symbol_id);\nCREATE INDEX IF NOT EXISTS idx_trades_account ON trades(account_id, traded_at DESC);\nCREATE INDEX IF NOT EXISTS idx_trades_symbol  ON trades(account_id, symbol_id, traded_at DESC);\nCREATE INDEX IF NOT EXISTS idx_snapshots_date    ON snapshots(date);\nCREATE INDEX IF NOT EXISTS idx_snapshots_account ON snapshots(account_id);\nCREATE INDEX IF NOT EXISTS idx_plans_account ON plans(account_id, created_at DESC);\nCREATE INDEX IF NOT EXISTS idx_plans_status  ON plans(account_id, status);\n"""

# Columns added after a book was first created. Applied to both books, since
# the ledger is one definition — a book-specific column belongs in that book's
# own extension schema instead.
_ADDED = {
    "accounts": [("kind", "TEXT NOT NULL DEFAULT 'human'"),
                 ("account_type", "TEXT"), ("mode", "INTEGER"),
                 ("paused_at", "INTEGER"),
                 ("sort_order", "INTEGER NOT NULL DEFAULT 0")],
    "holdings": [("name", "TEXT"), ("currency", "TEXT"),
                 ("entry_plan_id", "INTEGER"), ("entry_rationale", "TEXT"),
                 ("opened_at", "INTEGER"),
                 ("sort_order", "INTEGER NOT NULL DEFAULT 0"),
                 ("updated_at", "INTEGER NOT NULL DEFAULT 0")],
    "plans": [("status", "TEXT NOT NULL DEFAULT 'armed'"),
              ("conditions", "TEXT"), ("conditional_order_id", "TEXT"),
              ("expires_at", "INTEGER"),
              ("source", "TEXT NOT NULL DEFAULT 'agent'"),
              ("cycle_id", "INTEGER")],
}

# Before plans.source existed, the quant UI told a human plan apart by a tag
# it appended to the note; in the human book every plan is the person's.
# Run once, when the column is first added.
_BACKFILL = {
    ("plans", "source"):
        "UPDATE plans SET source = 'user' WHERE note LIKE '%[사용자 작성]%'"
        " OR account_id IN (SELECT account_id FROM accounts WHERE kind = 'human')",
}


def apply(conn: sqlite3.Connection) -> None:
    """Create the ledger tables, widen any a live book predates, then index.

    The three steps are ordered, not cosmetic: an index on a column an older
    book has not grown yet fails the whole script, and CREATE TABLE IF NOT
    EXISTS will not add the column for us.
    """
    conn.executescript(TABLES)
    for table, cols in _ADDED.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in cols:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                if (table, name) in _BACKFILL:
                    conn.execute(_BACKFILL[(table, name)])
    conn.executescript(INDEXES)
    conn.commit()
