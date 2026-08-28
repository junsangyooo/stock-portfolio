"""One-shot: parquet/json market data + holding_themes → data/market.db.

Reads the old per-symbol tree (data/market/{ohlcv,valuation,financials,sector,
etf}) and portfolio.db's `holding_themes`, writes the symbol book, then counts
both sides and refuses to call it a success unless they agree.

Nothing is deleted. Verify the counts, keep the tree around for a few days,
remove it by hand.

    ssh oracle-instance "cd ~/portfolio-dashboard && .venv/bin/python \\
        deploy/migrate_market_db.py"
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core import paths                                    # noqa: E402
from src.market.store.db import connect                       # noqa: E402

MARKET = paths.MARKET_DIR


def _symbols_in(subdir: str, suffix: str) -> list[tuple[str, Path]]:
    """[(symbol_id, path)] for data/market/{subdir}/{MARKET}/{code}{suffix}."""
    out = []
    root = MARKET / subdir
    if not root.exists():
        return out
    for mdir in sorted(root.iterdir()):
        if not mdir.is_dir():
            continue
        for f in sorted(mdir.glob(f"*{suffix}")):
            out.append((f"{mdir.name.upper()}:{f.stem}", f))
    return out


def _rows(df: pd.DataFrame, cols: list[str]) -> list[tuple]:
    """DataFrame → tuples in `cols` order, NaN → None. Missing columns are
    filled with None so an older parquet without a later column still loads."""
    out = []
    for r in df.itertuples(index=False):
        d = r._asdict()
        out.append(tuple(
            None if (c not in d or pd.isna(d[c])) else d[c] for c in cols))
    return out


def migrate_ohlcv(c: sqlite3.Connection) -> tuple[int, int]:
    cols = ["ts", "open", "high", "low", "close", "volume"]
    src_rows = 0
    for sid, path in _symbols_in("ohlcv", ".parquet"):
        df = pd.read_parquet(path)
        src_rows += len(df)
        c.executemany(
            "INSERT OR REPLACE INTO ohlcv "
            "(symbol_id, ts, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?)",
            [(sid, *row) for row in _rows(df, cols)])
    c.commit()
    return src_rows, c.execute("SELECT COUNT(*) FROM ohlcv").fetchone()[0]


def migrate_valuation(c: sqlite3.Connection) -> tuple[int, int]:
    cols = ["ts", "per", "pbr", "psr", "eps", "bps", "dps", "div_yield",
            "market_cap"]
    src_rows = 0
    for sid, path in _symbols_in("valuation", ".parquet"):
        df = pd.read_parquet(path)
        src_rows += len(df)
        c.executemany(
            "INSERT OR REPLACE INTO valuation (symbol_id, " + ", ".join(cols) +
            ") VALUES (" + ",".join("?" * (len(cols) + 1)) + ")",
            [(sid, *row) for row in _rows(df, cols)])
    c.commit()
    return src_rows, c.execute("SELECT COUNT(*) FROM valuation").fetchone()[0]


def migrate_financials(c: sqlite3.Connection) -> tuple[int, int]:
    cols = ["period", "fs_div", "report_date", "source",
            "revenue", "gross_profit", "operating_income", "net_income", "eps",
            "total_assets", "total_liabilities", "total_equity",
            "current_assets", "current_liabilities",
            "operating_cf", "investing_cf", "financing_cf"]
    src_rows = 0
    for sid, path in _symbols_in("financials", ".parquet"):
        df = pd.read_parquet(path)
        src_rows += len(df)
        # filed_at is left NULL: the old tree never recorded when a statement
        # was actually disclosed, and inventing it would defeat the whole point
        # of the column (event-driven refresh compares against it).
        c.executemany(
            "INSERT OR REPLACE INTO financials (symbol_id, " + ", ".join(cols) +
            ") VALUES (" + ",".join("?" * (len(cols) + 1)) + ")",
            [(sid, *row) for row in _rows(df, cols)])
    c.commit()
    return src_rows, c.execute("SELECT COUNT(*) FROM financials").fetchone()[0]


def migrate_sector(c: sqlite3.Connection) -> tuple[int, int]:
    n = 0
    for sid, path in _symbols_in("sector", ".json"):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            print(f"  skip unreadable {path}")
            continue
        c.execute(
            "INSERT OR REPLACE INTO sector "
            "(symbol_id, sector, industry, source, updated_at) VALUES (?,?,?,?,?)",
            (sid, rec.get("sector"), rec.get("industry"),
             rec.get("source") or "yfinance", rec.get("updated_at") or ""))
        n += 1
    c.commit()
    return n, c.execute("SELECT COUNT(*) FROM sector").fetchone()[0]


def migrate_etf(c: sqlite3.Connection) -> tuple[int, int]:
    n = 0
    for sid, path in _symbols_in("etf", ".json"):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            print(f"  skip unreadable {path}")
            continue
        updated = rec.pop("updated_at", "")
        rec.pop("symbol_id", None)
        c.execute(
            "INSERT OR REPLACE INTO etf (symbol_id, snapshot, updated_at) "
            "VALUES (?,?,?)",
            (sid, json.dumps(rec, ensure_ascii=False), updated))
        n += 1
    c.commit()
    return n, c.execute("SELECT COUNT(*) FROM etf").fetchone()[0]


def migrate_themes(c: sqlite3.Connection) -> tuple[int, int]:
    """portfolio.db.holding_themes → market.db.symbol_themes.

    The source table may already be gone (this script re-run after a deploy
    that dropped it from the schema), in which case there is nothing to move.
    """
    if not paths.DB_PATH.exists():
        return 0, 0
    src = sqlite3.connect(paths.DB_PATH)
    try:
        exists = src.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='holding_themes'").fetchone()
        if not exists:
            print("  holding_themes already gone from portfolio.db — skipping")
            return 0, c.execute(
                "SELECT COUNT(*) FROM symbol_themes").fetchone()[0]
        rows = src.execute(
            "SELECT symbol_id, theme_id, relation, weight, source, updated_at "
            "FROM holding_themes").fetchall()
    finally:
        src.close()
    c.executemany(
        "INSERT OR REPLACE INTO symbol_themes "
        "(symbol_id, theme_id, relation, weight, source, updated_at) "
        "VALUES (?,?,?,?,?,?)", rows)
    c.commit()
    return len(rows), c.execute("SELECT COUNT(*) FROM symbol_themes").fetchone()[0]


STEPS = [
    ("ohlcv", migrate_ohlcv),
    ("valuation", migrate_valuation),
    ("financials", migrate_financials),
    ("sector", migrate_sector),
    ("etf", migrate_etf),
    ("symbol_themes", migrate_themes),
]


def main() -> int:
    print(f"source tree : {MARKET}")
    print(f"target      : {paths.MARKET_DB_PATH}")
    print()
    bad = []
    with connect() as c:
        for name, fn in STEPS:
            src_n, dst_n = fn(c)
            ok = dst_n >= src_n          # >= : a re-run finds rows already there
            print(f"  {name:<14} source={src_n:<8} market.db={dst_n:<8} "
                  f"{'ok' if ok else 'MISMATCH'}")
            if not ok:
                bad.append(name)
    print()
    if bad:
        print(f"FAILED — {', '.join(bad)} lost rows. Nothing was deleted; "
              f"fix and re-run.")
        return 1
    print("ok — counts agree. The old tree is untouched; remove it by hand "
          "once you have watched the dashboard for a few days.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
