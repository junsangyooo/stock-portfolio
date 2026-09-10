"""Build the on-disk symbol master cache (data/universe/{kr,us}.json).

Run once a day via cron, after market close. Total payload ~ a few MB so
fits comfortably in memory. Each refresh writes atomically (.tmp → replace)
so a failed run never corrupts the previous good cache.

Sources:
  KR  — pykrx (KOSPI|KOSDAQ) + FinanceDataReader StockListing('ETF/KR')
  US  — FinanceDataReader StockListing('NASDAQ'|'NYSE'|'AMEX')

A build reports whether every one of its sources answered. A region whose
sources partly failed never overwrites the previous master (see refresh):
one dead exchange would otherwise drop every one of its symbols from the
master while the file still looks freshly built.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from typing import List

from ..core.kst import KST
from ..core.paths import UNIVERSE_DIR, ensure_dirs

log = logging.getLogger(__name__)


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

def build_kr() -> tuple[List[dict], bool]:
    """KOSPI + KOSDAQ + KR ETF, deduped on (market, code).

    Returns (items, all_sources_ok).
    """
    import FinanceDataReader as fdr  # type: ignore
    from pykrx import stock  # type: ignore

    out: dict[tuple[str, str], dict] = {}
    ok = True

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

    # KOSPI + KOSDAQ — one call per market gives ticker + name. The window
    # starts a week back so a pre-open run (cron is 07:30) still lands on a
    # session with rows.
    try:
        todate = stock.get_nearest_business_day_in_a_week()
        fromdate = (datetime.strptime(todate, "%Y%m%d")
                    - timedelta(days=7)).strftime("%Y%m%d")
    except Exception as e:  # noqa: BLE001
        log.error("universe KRX business day lookup failed: %s", e)
        todate = fromdate = ""
        ok = False

    if todate:
        for market, suffix in (("KOSPI", ".KS"), ("KOSDAQ", ".KQ")):
            try:
                df = stock.get_market_price_change_by_ticker(
                    fromdate, todate, market=market)
                if df.empty:
                    raise ValueError("empty listing")
                for code, r in df.iterrows():
                    _add(market, _safe(code), _safe(r.get("종목명")),
                         yf_suffix=suffix)
            except Exception as e:  # noqa: BLE001 — one dead source must not kill the rest
                log.error("universe %s listing failed: %s", market, e)
                ok = False

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
    except Exception as e:  # noqa: BLE001
        log.error("universe KR ETF listing failed: %s", e)
        ok = False

    return list(out.values()), ok


# ── US ─────────────────────────────────────────────────────────────────

def build_us() -> tuple[List[dict], bool]:
    """NASDAQ + NYSE + AMEX. Returns (items, all_sources_ok)."""
    import FinanceDataReader as fdr  # type: ignore

    out: dict[tuple[str, str], dict] = {}
    ok = True

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
        except Exception as e:  # noqa: BLE001 — one dead exchange must not kill the rest
            log.error("universe %s listing failed: %s", ex, e)
            ok = False

    return list(out.values()), ok


# ── orchestrator ───────────────────────────────────────────────────────

def refresh(region: str = "all") -> dict:
    ensure_dirs()
    started = time.time()
    summary: dict = {"started_at": _now_iso(), "regions": {}, "kept_previous": []}

    for reg, build in (("kr", build_kr), ("us", build_us)):
        if region not in ("all", reg):
            continue
        items, ok = build()
        path = UNIVERSE_DIR / f"{reg}.json"
        # A listing source can be down while its siblings answer. Writing that
        # partial master over a good one silently drops whole exchanges, so any
        # source failure keeps the previous file and reports it.
        if (not items or not ok) and path.exists():
            log.error("universe %s incomplete (%d items, sources_ok=%s) — "
                      "keeping the previous file", reg, len(items), ok)
            summary["kept_previous"].append(reg)
            continue
        _atomic_write(path, {"updated_at": _now_iso(), "region": reg,
                             "n": len(items), "items": items})
        summary["regions"][reg] = len(items)

    summary["elapsed_s"] = round(time.time() - started, 2)
    _atomic_write(UNIVERSE_DIR / "updated_at.json", summary)
    return summary
