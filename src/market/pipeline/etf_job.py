"""CLI entry-point for the daily ETF snapshot (cron 06:15 KST).

One HTTP call to Naver returns ALL listed KR ETFs; we then fan it out to the
per-symbol json store for every ETF we actually hold. Cheap (~1 req, ~600KB)
and necessary because:
- pykrx ETF APIs require KRX login (KRX_ID/KRX_PW),
- yfinance returns None for Korean ETF metadata (esp. new 2024+ alphanumeric
  tickers like 0148J0 / 0190C0),
- the fundamental agent skips ETFs entirely without this data.

This is the COLLECT half. The fundamental agent's ETF branch reads from
etf_store on-demand (no LLM here).

Usage:
  python -m src.market.pipeline.etf_job [--symbol KR:292160]

Designed for cron — exits 0 on partial success.
"""
from __future__ import annotations

import argparse
import json
import sys

from ...core import kst
from ...core.paths import split_symbol
from ..source import naver_etf_source
from ..store import etf_store
from ...universe import search as uni_search


def _summary(total: int, ok: int, results: list) -> dict:
    return {"date": kst.today(), "total": total, "ok": ok, "results": results}


def run(symbols) -> dict:
    """Snapshot the ETFs among `symbols`.

    `symbols` is required — this is the shared market layer and it does not ask
    an account book what it holds. Non-ETFs in the list are skipped, so the
    caller can hand over its whole book without filtering first.
    """
    symbols = [s for s in symbols
               if (uni_search.get(s) or {}).get("is_etf")]
    if not symbols:
        return _summary(0, 0, [])

    # one fetch covers them all — Naver returns the full ETF list.
    all_items = naver_etf_source.fetch_all()
    if not all_items:
        return _summary(len(symbols), 0,
                        [{"symbol_id": s, "fatal": "naver fetch failed"}
                         for s in symbols])

    results, ok = [], 0
    for sid in symbols:
        item = all_items.get(split_symbol(sid)[1])
        if not item:
            results.append({"symbol_id": sid, "fatal": "not in Naver ETF list"})
            continue
        try:
            snap = naver_etf_source.shape(item)
            rec = etf_store.write(sid, snap)
            results.append({"symbol_id": sid, "nav": rec.get("nav"),
                            "premium_pct": rec.get("premium_pct"),
                            "aum_eokwon": rec.get("aum_eokwon")})
            ok += 1
        except Exception as e:  # noqa: BLE001 — per-symbol non-fatal
            results.append({"symbol_id": sid, "fatal": str(e)[:200]})
    return _summary(len(symbols), ok, results)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", required=True,
                    help="comma-separated symbol_ids (non-ETFs are skipped)")
    args = ap.parse_args()
    symbols = [x.strip().upper() for x in args.symbols.split(",") if x.strip()]
    summary = run(symbols)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if (summary["total"] == 0 or summary["ok"] > 0) else 1


if __name__ == "__main__":
    sys.exit(main())
