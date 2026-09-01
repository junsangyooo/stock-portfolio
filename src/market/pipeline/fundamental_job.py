"""Fundamental data collection for a given set of symbols.

Backfills valuation (L1) + financials (L2). Financials
change only on quarterly disclosure, so once-daily is plenty; this runs
alongside the qualitative news batch at 06:00 KST.

This is the COLLECT half (data → parquet stores). The JUDGE half (fundamental
agent) is per-symbol and runs on-demand when the user clicks the 재무 분석
button — intentionally NOT here.

Usage:
  python -m src.market.pipeline.fundamental_job [--symbol KR:005930] [--years 4]

Designed for cron — exits 0 on partial success, non-zero only if every symbol
failed.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from ...core.kst import KST as _KST
from typing import Optional

from . import fundamental_backfill



def _refresh_sector(sid: str) -> Optional[str]:
    """Refetch the sector tag if the cache is missing/stale. Best-effort —
    returns the resolved sector (or None) and never raises."""
    from ..store import sector_store
    if sector_store.is_fresh(sid):
        rec = sector_store.read(sid) or {}
        return rec.get("sector")
    try:
        from ..source import sector_source
        tag = sector_source.fetch(sid)
        sector_store.write(sid, tag.get("sector"), tag.get("industry"))
        return tag.get("sector")
    except Exception:  # noqa: BLE001
        return None


def run(symbols, years: int = 4) -> dict:
    """Backfill financials + valuation (+ sector tag) for each symbol.

    `symbols` is required. This module belongs to the shared market layer and
    does not get to ask an account book what it holds — whoever schedules the
    job supplies the list (src/portfolio/jobs.py for the human accounts).
    """
    symbols = list(symbols)
    results, ok = [], 0
    for sid in symbols:
        try:
            r = fundamental_backfill.fill_one(sid, years=years)
            r["sector"] = _refresh_sector(sid)
            results.append(r)
            ok += 1
        except Exception as e:  # noqa: BLE001 — keep going, report per-symbol
            results.append({"symbol_id": sid, "fatal": str(e)[:200]})
    return {"date": datetime.now(_KST).strftime("%Y-%m-%d"),
            "total": len(symbols), "ok": ok, "results": results}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", required=True,
                    help="comma-separated symbol_ids")
    ap.add_argument("--years", type=int, default=4, help="years of financials to fetch")
    args = ap.parse_args()

    symbols = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]
    summary = run(symbols, years=args.years)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    # non-zero only if there were symbols and none succeeded
    return 0 if (summary["total"] == 0 or summary["ok"] > 0) else 1


if __name__ == "__main__":
    sys.exit(main())
