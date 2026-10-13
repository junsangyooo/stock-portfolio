"""Whole-market KR factor table for screening (PER/PBR/DIV + market cap).

pykrx returns the ENTIRE KRX market's daily fundamentals in one call, which
is the cheapest legal screening base we have — no per-symbol loops, no KIS.
Cached one parquet per trading day under data/market/screener/KR/; the first
screen of the day pays the ~2-5s fetch, the rest read the file.

Columns: per, pbr, eps, bps, div (배당수익률 %), dps, mcap_eok (시총 억원),
value_eok (거래대금 억원). Index = 6-digit ticker.

Per §10.1 source-failure rule: an empty fetch raises instead of silently
returning an empty table (the 2026 "pykrx fundamental death" went unnoticed
for weeks exactly because of silent empties).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import pandas as pd
from pykrx import stock as pkx  # type: ignore

from ...core.kst import KST
from ...core.paths import MARKET_DIR

log = logging.getLogger(__name__)

SCREENER_DIR = MARKET_DIR / "screener" / "KR"


def _try_day(ymd: str) -> pd.DataFrame | None:
    fund = pkx.get_market_fundamental_by_ticker(ymd, market="ALL")
    # Holiday/weekend → all-zero or empty frame; treat as "no data".
    if fund is None or fund.empty or float(fund["PER"].abs().sum()) == 0:
        return None
    cap = pkx.get_market_cap_by_ticker(ymd, market="ALL")
    df = fund.join(cap, how="left")
    out = pd.DataFrame({
        "per": df["PER"], "pbr": df["PBR"], "eps": df["EPS"],
        "bps": df["BPS"], "div": df["DIV"], "dps": df["DPS"],
        "mcap_eok": (df["시가총액"] / 1e8).round(0),
        "value_eok": (df["거래대금"] / 1e8).round(1),
    })
    out.index.name = "code"
    return out


def load() -> tuple[str, pd.DataFrame]:
    """Latest trading day's factor table, cached. Returns (yyyymmdd, df)."""
    today = datetime.now(KST)
    for back in range(8):
        ymd = (today - timedelta(days=back)).strftime("%Y%m%d")
        path = SCREENER_DIR / f"{ymd}.parquet"
        if path.exists():
            return ymd, pd.read_parquet(path)
        try:
            df = _try_day(ymd)
        except Exception as e:  # noqa: BLE001 — walk back, but leave a trace
            log.warning("screener fetch %s failed: %s", ymd, e)
            df = None
        if df is not None:
            SCREENER_DIR.mkdir(parents=True, exist_ok=True)
            df.to_parquet(path)
            log.info("screener cached %s (%d tickers)", ymd, len(df))
            return ymd, df
    raise RuntimeError("KRX screener: no fundamental data in the last 8 days "
                       "(KRX_ID/KRX_PW not set, or pykrx outage? see data/logs)")


if __name__ == "__main__":
    # Daily cron entry: warm the cache OUTSIDE the chat subprocess — the chat
    # child has KRX_* scrubbed from its env (core/llm.py), so screening from
    # chat only ever reads the parquet this writes.
    import sys
    logging.basicConfig(level=logging.INFO)
    try:
        ymd, df = load()
        print(f"screener ok {ymd}: {len(df)} tickers")
    except Exception as e:  # noqa: BLE001 — cron convention: non-zero + message
        print(f"screener FAILED: {e}", file=sys.stderr)
        sys.exit(1)
