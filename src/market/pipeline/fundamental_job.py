"""Fundamental data collection for a given set of symbols.

Backfills valuation (L1) + financials (L2). Financials
change only on quarterly disclosure, so once-daily is plenty; this runs
alongside the qualitative news batch at 06:00 KST.

This only collects (sources → market.db). Judgment is the comprehensive
analysis (src/research/analyze), which reads what is stored here.

Usage:
  python -m src.market.pipeline.fundamental_job --symbols KR:005930,US:NVDA [--years 4]

Designed for cron — exits 0 on partial success, non-zero only if every symbol
failed. A symbol whose sector tag could not be fetched keeps its stored tag
and is named in `sector_failed` (the sources page counts it as a problem).
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from ...core import kst
from . import fundamental_backfill


def _refresh_sector(sid: str) -> tuple[Optional[str], Optional[str]]:
    """Refetch the sector tag if the cache is missing/stale. Returns
    (sector, error): on a failed fetch the stored tag stays as it was — an
    error is not "no sector" — and the error is the caller's to report."""
    from ..store import sector_store
    stored = (sector_store.read(sid) or {}).get("sector")
    if sector_store.is_fresh(sid):
        return stored, None
    from ..source import sector_source
    try:
        tag = sector_source.fetch(sid)
    except Exception as e:  # noqa: BLE001 — SourceError and kin, per symbol
        return stored, str(e)[:200]
    sector_store.write(sid, tag.get("sector"), tag.get("industry"))
    return tag.get("sector"), None


def run(symbols, years: int = 4) -> dict:
    """Backfill financials + valuation (+ sector tag) for each symbol.

    `symbols` is required. This module belongs to the shared market layer and
    does not get to ask an account book what it holds — whoever schedules the
    job supplies the list (src/portfolio/supply_job.py for the human accounts).
    """
    symbols = list(symbols)
    results, ok = [], 0
    for sid in symbols:
        try:
            r = fundamental_backfill.fill_one(sid, years=years)
            r["sector"], err = _refresh_sector(sid)
            if err:
                r["sector_error"] = err
            results.append(r)
            ok += 1
        except Exception as e:  # noqa: BLE001 — keep going, report per-symbol
            results.append({"symbol_id": sid, "fatal": str(e)[:200]})
    return {"date": kst.today(), "total": len(symbols), "ok": ok,
            "failed": [r["symbol_id"] for r in results if "fatal" in r],
            "sector_failed": [r["symbol_id"] for r in results if "sector_error" in r],
            "results": results}


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
