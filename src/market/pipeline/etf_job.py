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
from datetime import datetime

from ...core.kst import KST as _KST

from ..source import naver_etf_source
from ..store import etf_store
from ...portfolio.store.holding_store import all_symbol_ids
from ...universe import search as uni_search



def _holding_etfs() -> list[str]:
    """ETF subset of current holdings (resolved via universe is_etf flag)."""
    out: list[str] = []
    for sid in all_symbol_ids():
        rec = uni_search.get(sid) or {}
        if rec.get("is_etf"):
            out.append(sid)
    return out


def run(symbols=None) -> dict:
    symbols = symbols or _holding_etfs()
    if not symbols:
        return {"date": datetime.now(_KST).strftime("%Y-%m-%d"),
                "total": 0, "ok": 0, "results": []}

    # one fetch covers them all — Naver returns the full ETF list.
    all_items = naver_etf_source.fetch_all()
    if not all_items:
        return {"date": datetime.now(_KST).strftime("%Y-%m-%d"),
                "total": len(symbols), "ok": 0,
                "results": [{"symbol_id": s, "fatal": "naver fetch failed"}
                            for s in symbols]}

    results, ok = [], 0
    for sid in symbols:
        _, code = sid.split(":", 1)
        item = all_items.get(code)
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
    return {"date": datetime.now(_KST).strftime("%Y-%m-%d"),
            "total": len(symbols), "ok": ok, "results": results}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default=None,
                    help="single symbol_id (default: all ETF holdings)")
    args = ap.parse_args()
    symbols = [args.symbol] if args.symbol else None
    summary = run(symbols=symbols)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if (summary["total"] == 0 or summary["ok"] > 0) else 1


if __name__ == "__main__":
    sys.exit(main())
