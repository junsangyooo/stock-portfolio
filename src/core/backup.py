"""Weekday-rotating SQLite backups — `VACUUM INTO`, WAL-safe.

    data/backup/{portfolio,news}.db.{mon..sun}
    data/backup/market.db.latest            # one slot, overwritten daily

market.db is regenerable but a 737MB recollection takes a day, so it gets a
single copy instead of the weekday rotation.

Each copy is written to a .tmp, opened and checked (integrity_check ok, at
least one table — a 0-byte source "vacuums" into an empty db), then renamed
over the slot. A failure on one book does not stop the others; the run still
raises at the end, because a silent backup failure is no backup.

    python -m src.core.backup [--only portfolio news market]

Restore (example: portfolio, Monday's copy):

    sudo systemctl stop dashboard.service
    cp data/backup/portfolio.db.mon data/portfolio.db
    rm -f data/portfolio.db-wal data/portfolio.db-shm
    sudo systemctl start dashboard.service
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from . import kst, paths


def _books() -> dict[str, Path]:
    # Read at call time so tests can redirect core.paths.
    return {"portfolio": paths.DB_PATH,
            "news": paths.NEWS_DB_PATH,
            "market": paths.MARKET_DB_PATH}


def _weekday() -> str:
    return kst.now().strftime("%a").lower()


def _slot(name: str) -> str:
    return "latest" if name == "market" else _weekday()


def backup_one(src: Path, dest_dir: Path, slot: str) -> dict:
    if not src.is_file():
        raise FileNotFoundError(src)
    final = dest_dir / f"{src.name}.{slot}"
    tmp = final.with_name(final.name + ".tmp")
    tmp.unlink(missing_ok=True)            # VACUUM INTO refuses an existing target
    try:
        with closing(sqlite3.connect(src)) as con:
            con.execute("VACUUM INTO ?", (str(tmp),))
        with closing(sqlite3.connect(tmp)) as con:
            ok = con.execute("PRAGMA integrity_check").fetchone()[0]
            n_tables = con.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        if ok != "ok":
            raise sqlite3.DatabaseError(f"integrity_check: {ok}")
        if n_tables == 0:
            raise sqlite3.DatabaseError("backup has no tables (empty source?)")
        os.replace(tmp, final)
    finally:
        tmp.unlink(missing_ok=True)
    return {"bytes": final.stat().st_size, "path": final}


def backup_all(only: list[str] | None = None) -> dict[str, dict]:
    dest = paths.BACKUP_DIR
    dest.mkdir(parents=True, exist_ok=True)
    books = _books()
    out: dict[str, dict] = {}
    errors: list[str] = []
    for name in (only or books):
        try:
            out[name] = backup_one(books[name], dest, _slot(name))
        except Exception as e:                 # keep going; raise once at the end
            errors.append(f"{name}: {type(e).__name__}: {e}")
    if errors:
        raise RuntimeError("backup failed — " + "; ".join(errors))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="+", choices=tuple(_books()))
    args = ap.parse_args(argv)
    try:
        out = backup_all(only=args.only)
    except Exception as e:
        from .notify import discord           # lazy: pulls httpx
        print(e, file=sys.stderr)
        discord.send(f"🛑 DB backup FAILED — {e}")
        return 1
    print(json.dumps({k: {"bytes": v["bytes"], "path": str(v["path"])}
                      for k, v in out.items()}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
