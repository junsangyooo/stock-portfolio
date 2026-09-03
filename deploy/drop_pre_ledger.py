"""One-shot: drop the pre-ledger vocabulary from both account books.

migrate_ledger.py left the old tables in place "until the counts have been
watched for a few days". Nothing reads them any more (ops notes §3.1, §12):

    portfolio.db   *_pre_ledger, balances, holding_themes
    quant.db       *_pre_ledger, account (singular), positions
    either         accounts.auto_trade   (column, unused since migrate_quant_db)

Also removed: `holdings` rows whose account_id is not in `accounts` — the
quant account's rows were left behind in portfolio.db and its symbols leak
into the human batch jobs.

Whitelist only: a table is dropped if its name ends in `_pre_ledger` or is
in DROP_TABLES, and never if it is a live ledger/extension table. Anything
else in the file is left alone.

    .venv/bin/python deploy/drop_pre_ledger.py [--book portfolio|quant|all]
    .venv/bin/python deploy/drop_pre_ledger.py --apply        # backup first
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core import backup, paths                          # noqa: E402

DROP_TABLES = {
    "portfolio": {"balances", "holding_themes"},
    "quant": {"account", "positions"},
}
LIVE = {"accounts", "holdings", "trades", "cash", "snapshots", "plans", "watch",
        "chat_sessions", "cycles", "journal", "orders"}
DROP_COLUMN_OK = sqlite3.sqlite_version_info >= (3, 35, 0)


def _db_path(book: str) -> Path:
    return paths.DB_PATH if book == "portfolio" else paths.QUANT_DB_PATH


def _tables(c) -> set:
    return {r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def _count(c, table: str) -> int:
    return c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def targets(c, book: str) -> list[str]:
    return sorted(t for t in _tables(c)
                  if t not in LIVE
                  and (t.endswith("_pre_ledger") or t in DROP_TABLES[book]))


def orphans(c) -> list[tuple]:
    return c.execute(
        "SELECT account_id, symbol_id, qty FROM holdings "
        "WHERE account_id NOT IN (SELECT account_id FROM accounts)").fetchall()


def has_auto_trade(c) -> bool:
    return "auto_trade" in {r[1] for r in c.execute("PRAGMA table_info(accounts)")}


def inspect(c, book: str) -> None:
    print(f"\n[{book}] {_db_path(book)}")
    print("  drop tables:")
    for t in targets(c, book):
        print(f"    {t:<28} {_count(c, t):>8} rows")
    print("  orphan holdings (account_id not in accounts):")
    for aid, sid, qty in orphans(c):
        print(f"    {aid:<10} {sid:<12} qty={qty}")
    print(f"  accounts.auto_trade: {'present' if has_auto_trade(c) else 'absent'}"
          + ("" if DROP_COLUMN_OK else f"  (sqlite {sqlite3.sqlite_version} < 3.35, kept)"))


def apply(c, book: str) -> None:
    before = (len(_tables(c)), _count(c, "holdings"))
    c.execute("BEGIN")
    for t in targets(c, book):
        c.execute(f"DROP TABLE {t}")
    c.execute("DELETE FROM holdings "
              "WHERE account_id NOT IN (SELECT account_id FROM accounts)")
    if has_auto_trade(c) and DROP_COLUMN_OK:
        c.execute("ALTER TABLE accounts DROP COLUMN auto_trade")
    c.execute("COMMIT")
    c.execute("VACUUM")
    after = (len(_tables(c)), _count(c, "holdings"))
    print(f"  applied: tables {before[0]} → {after[0]}, "
          f"holdings {before[1]} → {after[1]}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book", choices=("portfolio", "quant", "all"), default="all")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    books = ["portfolio", "quant"] if args.book == "all" else [args.book]

    if args.apply:
        try:
            backup.backup_all(only=books)
        except Exception as e:
            print(f"backup failed, nothing changed: {e}", file=sys.stderr)
            return 1
    for book in books:
        c = sqlite3.connect(_db_path(book), isolation_level=None)
        try:
            inspect(c, book)
            if args.apply:
                apply(c, book)
        except Exception as e:
            print(f"  FAILED: {e}", file=sys.stderr)
            return 1
        finally:
            c.close()
    if not args.apply:
        print("\ndry-run — nothing changed (add --apply)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
