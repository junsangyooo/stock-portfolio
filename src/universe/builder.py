"""Build the on-disk symbol master cache (data/universe/{kr,us}.json).

Run once a day via cron, after market close. Total payload ~ a few MB so
fits comfortably in memory. Each refresh writes atomically (.tmp → replace)
so a failed run never corrupts the previous good cache.

Sources:
  KR  — FinanceDataReader StockListing('KOSPI'|'KOSDAQ'|'ETF/KR')
  US  — FinanceDataReader StockListing('NASDAQ'|'NYSE'|'AMEX')
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import List

from ..core.paths import UNIVERSE_DIR, ensure_dirs

KST = timezone(timedelta(hours=9))


def _safe(v) -> str:
    if v is None:
        return ""
    try:
        s = str(v).strip()
    except Exception:
        return ""
    return "" if s.lower() in ("nan", "none") else s


def _now_iso() -> str:
    return datetime.now(KST).isoformat()


def _atomic_write(path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False))
    tmp.replace(path)


# ── KR ─────────────────────────────────────────────────────────────────

def build_kr() -> List[dict]:
    """KOSPI + KOSDAQ + KR ETF, deduped on (market, code)."""
    import FinanceDataReader as fdr  # type: ignore

    out: dict[tuple[str, str], dict] = {}

    def _add(market: str, code: str, name: str, sector: str = "",
             is_etf: bool = False, yf_suffix: str = ""):
        if not code or not name:
            return
        key = (market, code)
        if key in out:
            return
        out[key] = {
            "symbol_id": f"KR:{code}",
            "market": market,             # 'KOSPI' | 'KOSDAQ' | 'ETF'
            "code": code,
            "name_kr": name,
            "name_en": None,
            "sector": sector or None,
            "currency": "KRW",
            "is_etf": is_etf,
            "yf_ticker": f"{code}{yf_suffix}",   # yfinance fallback
        }

    # KOSPI
    try:
        df = fdr.StockListing("KOSPI")
        for _, r in df.iterrows():
            _add("KOSPI",
                 _safe(r.get("Code") or r.get("Symbol")),
                 _safe(r.get("Name")),
                 _safe(r.get("Sector")) if "Sector" in r else "",
                 yf_suffix=".KS")
    except Exception:
        pass

    # KOSDAQ
    try:
        df = fdr.StockListing("KOSDAQ")
        for _, r in df.iterrows():
            _add("KOSDAQ",
                 _safe(r.get("Code") or r.get("Symbol")),
                 _safe(r.get("Name")),
                 _safe(r.get("Sector")) if "Sector" in r else "",
                 yf_suffix=".KQ")
    except Exception:
        pass

    # KR ETF
    try:
        df = fdr.StockListing("ETF/KR")
        for _, r in df.iterrows():
            _add("ETF",
                 _safe(r.get("Symbol") or r.get("Code")),
                 _safe(r.get("Name")),
                 sector="ETF",
                 is_etf=True,
                 yf_suffix=".KS")
    except Exception:
        pass

    return list(out.values())


# ── US ─────────────────────────────────────────────────────────────────

def build_us() -> List[dict]:
    """NASDAQ + NYSE + AMEX."""
    import FinanceDataReader as fdr  # type: ignore

    out: dict[tuple[str, str], dict] = {}

    def _add(market: str, code: str, name: str, sector: str = ""):
        if not code or not name:
            return
        key = (market, code)
        if key in out:
            return
        out[key] = {
            "symbol_id": f"US:{code}",
            "market": market,             # 'NASDAQ' | 'NYSE' | 'AMEX'
            "code": code,
            "name_kr": None,
            "name_en": name,
            "sector": sector or None,
            "currency": "USD",
            "is_etf": False,
            "yf_ticker": code,
        }

    for ex in ("NASDAQ", "NYSE", "AMEX"):
        try:
            df = fdr.StockListing(ex)
            for _, r in df.iterrows():
                _add(ex,
                     _safe(r.get("Symbol") or r.get("Code")),
                     _safe(r.get("Name")),
                     _safe(r.get("Sector")) if "Sector" in r else "")
        except Exception:
            continue

    return list(out.values())


# ── orchestrator ───────────────────────────────────────────────────────

def refresh(region: str = "all") -> dict:
    ensure_dirs()
    started = time.time()
    summary: dict = {"started_at": _now_iso(), "regions": {}}

    if region in ("all", "kr"):
        items = build_kr()
        _atomic_write(
            UNIVERSE_DIR / "kr.json",
            {"updated_at": _now_iso(), "region": "kr", "n": len(items),
             "items": items},
        )
        summary["regions"]["kr"] = len(items)

    if region in ("all", "us"):
        items = build_us()
        _atomic_write(
            UNIVERSE_DIR / "us.json",
            {"updated_at": _now_iso(), "region": "us", "n": len(items),
             "items": items},
        )
        summary["regions"]["us"] = len(items)

    summary["elapsed_s"] = round(time.time() - started, 2)
    _atomic_write(UNIVERSE_DIR / "updated_at.json", summary)
    return summary
