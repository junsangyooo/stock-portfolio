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

from ..core import kst
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
    return kst.now().isoformat()


def _atomic_write(path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False))
    tmp.replace(path)


def _add(out: dict, region: str, market: str, code: str, name: str, *,
         sector: str = "", is_etf: bool = False, yf_suffix: str = "") -> None:
    """One listing row into `out`, deduped on (market, code). `market` is
    KOSPI | KOSDAQ | ETF for KR, NASDAQ | NYSE | AMEX for US."""
    if not code or not name or (market, code) in out:
        return
    kr = region == "KR"
    out[(market, code)] = {
        "symbol_id": f"{region}:{code}",
        "market": market,
        "code": code,
        "name_kr": name if kr else None,
        "name_en": None if kr else name,
        "sector": sector or None,
        "currency": "KRW" if kr else "USD",
        "is_etf": is_etf,
        "yf_ticker": f"{code}{yf_suffix}",   # yfinance fallback
    }


# ── KR ─────────────────────────────────────────────────────────────────

def build_kr() -> tuple[List[dict], bool]:
    """KOSPI + KOSDAQ + KR ETF, deduped on (market, code).

    Returns (items, all_sources_ok).
    """
    import FinanceDataReader as fdr  # type: ignore
    from pykrx import stock  # type: ignore

    out: dict[tuple[str, str], dict] = {}
    ok = True

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
                    _add(out, "KR", market, _safe(code), _safe(r.get("종목명")),
                         yf_suffix=suffix)
            except Exception as e:  # noqa: BLE001 — one dead source must not kill the rest
                log.error("universe %s listing failed: %s", market, e)
                ok = False

    # KR ETF
    try:
        df = fdr.StockListing("ETF/KR")
        for _, r in df.iterrows():
            _add(out, "KR", "ETF", _safe(r.get("Symbol") or r.get("Code")),
                 _safe(r.get("Name")), sector="ETF", is_etf=True, yf_suffix=".KS")
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

    for ex in ("NASDAQ", "NYSE", "AMEX"):
        try:
            df = fdr.StockListing(ex)
            for _, r in df.iterrows():
                _add(out, "US", ex, _safe(r.get("Symbol") or r.get("Code")),
                     _safe(r.get("Name")), sector=_safe(r.get("Sector")))
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
