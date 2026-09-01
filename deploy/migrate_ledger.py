"""One-shot: move both account books onto the shared ledger schema.

    portfolio.db   balances → cash, + trades / watch, holdings & plans widened
    quant.db       account → accounts, positions → holdings,
                   account_id added to every ledger row, + trades

Both books already carried the same ledger under two vocabularies. Applying
the shared schema creates the new tables; this copies the rows across and
counts both sides before calling it done. Nothing is dropped — the old tables
stay until the counts have been watched for a few days.

    ssh oracle-instance "cd ~/portfolio-dashboard && .venv/bin/python \\
        deploy/migrate_ledger.py"
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core import paths                                    # noqa: E402
from src.ledger import apply as apply_ledger                  # noqa: E402
from src.quant.db import DEFAULT_ACCOUNT_ID                   # noqa: E402


def _tables(c) -> set:
    return {r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def _cols(c, table: str) -> list:
    return [r[1] for r in c.execute(f"PRAGMA table_info({table})")]


def _count(c, table: str) -> int:
    return c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _copy(c, src: str, dst: str, *, const: dict | None = None) -> int:
    """Copy rows src → dst on their shared columns, plus fixed values."""
    const = const or {}
    shared = [x for x in _cols(c, src) if x in _cols(c, dst)]
    cols = shared + list(const)
    marks = ", ".join(["?"] * len(const))
    sel = ", ".join(shared) + (f", {marks}" if const else "")
    c.execute(f"INSERT OR IGNORE INTO {dst} ({', '.join(cols)}) "
              f"SELECT {sel} FROM {src}", list(const.values()))
    return c.total_changes


def migrate_portfolio() -> dict:
    out = {}
    conn = sqlite3.connect(paths.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        apply_ledger(conn)
        have = _tables(conn)
        if "balances" in have:
            before = conn.total_changes
            _copy(conn, "balances", "cash")
            out["balances→cash"] = (_count(conn, "balances"),
                                    conn.total_changes - before)
        # holdings/plans gained columns in place; nothing to move.
        now = int(time.time())
        conn.execute("UPDATE holdings SET updated_at = ? WHERE updated_at = 0",
                     (now,))
        conn.execute("UPDATE accounts SET kind = 'human' WHERE kind IS NULL")
        conn.commit()
        out["accounts"] = _count(conn, "accounts")
        out["holdings"] = _count(conn, "holdings")
        out["cash"] = _count(conn, "cash")
        out["plans"] = _count(conn, "plans")
        out["trades"] = _count(conn, "trades")
    finally:
        conn.close()
    return out


def migrate_quant() -> dict:
    """quant.db predates account_id, and four of its tables are keyed without
    it — cash(currency), snapshots(date,currency), watch(symbol_id),
    plans(plan_id). A column cannot be added to a primary key, so those are
    moved aside and the ledger's versions built fresh beside them.
    """
    out: dict = {}
    conn = sqlite3.connect(paths.QUANT_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=OFF")   # we fill the parent below
        # Whatever the live row calls itself wins — the constant is only a
        # fallback for an empty book.
        quant_id = DEFAULT_ACCOUNT_ID
        if "account" in _tables(conn):
            row = conn.execute("SELECT account_id FROM account LIMIT 1").fetchone()
            if row:
                quant_id = row[0]
        out["account_id"] = quant_id

        # Step the old shapes aside so the ledger can create the real ones.
        moved = {}
        for t in ("cash", "snapshots", "plans", "watch"):
            if t in _tables(conn) and "account_id" not in _cols(conn, t):
                conn.execute(f"ALTER TABLE {t} RENAME TO {t}_pre_ledger")
                moved[t] = f"{t}_pre_ledger"
        conn.commit()

        apply_ledger(conn)
        if "entry_cycle_id" not in _cols(conn, "holdings"):
            conn.execute("ALTER TABLE holdings ADD COLUMN entry_cycle_id INTEGER")

        # accounts first: everything else references it.
        if "account" in _tables(conn):
            src = _count(conn, "account")
            _copy(conn, "account", "accounts", const={"kind": "auto"})
            conn.execute("UPDATE accounts SET kind = 'auto' "
                         "WHERE kind IS NULL OR kind = 'human'")
            out["account→accounts"] = (src, _count(conn, "accounts"))

        if "positions" in _tables(conn):
            src = _count(conn, "positions")
            now = int(time.time())
            _copy(conn, "positions", "holdings",
                  const={"account_id": quant_id, "added_at": now,
                         "updated_at": now})
            out["positions→holdings"] = (src, _count(conn, "holdings"))

        for t, old in moved.items():
            src = _count(conn, old)
            _copy(conn, old, t, const={"account_id": quant_id})
            out[f"{old}→{t}"] = (src, _count(conn, t))

        conn.commit()
        for t in ("accounts", "holdings", "cash", "snapshots", "plans",
                  "watch", "trades", "orders", "cycles", "journal"):
            out[t] = _count(conn, t)
    finally:
        conn.close()
    return out


def main() -> int:
    print(f"portfolio : {paths.DB_PATH}")
    print(f"quant     : {paths.QUANT_DB_PATH}\n")
    p = migrate_portfolio()
    print("portfolio.db")
    for k, v in p.items():
        print(f"  {k:<20} {v}")
    q = migrate_quant()
    print("\nquant.db")
    for k, v in q.items():
        print(f"  {k:<20} {v}")

    bad = []
    for name, pair in list(p.items()) + list(q.items()):
        if isinstance(pair, tuple) and pair[1] < pair[0]:
            bad.append(f"{name}: {pair[0]} → {pair[1]}")
    print()
    if bad:
        print("FAILED — rows lost:\n  " + "\n  ".join(bad))
        return 1
    print("ok — old tables left in place; remove them by hand once the "
          "dashboard has been watched for a few days.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
