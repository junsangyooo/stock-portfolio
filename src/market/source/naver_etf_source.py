"""Naver Finance ETF list — single-shot snapshot for ALL KR ETFs.

Endpoint  : https://finance.naver.com/api/sise/etfItemList.nhn
Encoding  : euc-kr (Naver legacy)
Fields per item:
  itemcode          '292160' / '0148J0' (new alphanumeric supported)
  itemname          'TIGER KRX300' (some entries arrive mojibake — kept as-is)
  nowVal            현재가 (KRW)
  nav               NAV (KRW)
  changeVal         전일대비
  changeRate        등락률 (%)
  threeMonthEarnRate 3개월 수익률 (%)
  marketSum         시가총액 (억원, AUM 근사)
  quant             거래량
  amonut            거래대금 (백만원)
  etfTabCode        분류 코드 (1=국내시장지수, 2=국내업종/테마, …)

This is the only ETF data source that:
- works without login (KRX requires KRX_ID/KRW),
- covers the new 2024+ alphanumeric ETF tickers (yfinance returns None for those),
- gives NAV + 3M return + AUM in one call.

We snapshot the full list once a day (cheap — 1 HTTP call, ~600KB), then fan
it out to per-symbol JSON via etf_store.
"""
from __future__ import annotations

import json
import urllib.request
from typing import Optional

_URL = "https://finance.naver.com/api/sise/etfItemList.nhn"
_UA = "portfolio-dashboard/1.0 (+etf-snapshot)"

# Naver's etfTabCode -> human label (used as 'category' fallback when KRX
# theme tagging is unavailable).
_TAB_LABEL = {
    1: "국내시장지수", 2: "국내업종/테마", 3: "국내파생",
    4: "해외주식", 5: "원자재", 6: "채권", 7: "기타",
}


def fetch_all(timeout: float = 12.0) -> dict[str, dict]:
    """Fetch the full Naver ETF list, keyed by itemcode (6-char ticker).

    Returns {} on network/parse failure rather than raising — the cron job
    keeps going and the per-symbol stale file stays usable for a day."""
    req = urllib.request.Request(_URL, headers={"User-Agent": _UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except Exception:  # noqa: BLE001 — caller treats {} as fetch failure
        return {}

    # Naver serves euc-kr; some new ETF names round-trip cleanly, others come
    # back mojibake — we keep them anyway (yf_ticker resolves the canonical
    # name via universe/search later, so the name here is just diagnostic).
    try:
        doc = json.loads(raw.decode("euc-kr", errors="replace"))
    except json.JSONDecodeError:
        return {}

    items = doc.get("result", {}).get("etfItemList", []) or []
    return {it["itemcode"]: it for it in items if it.get("itemcode")}


def shape(item: dict) -> dict:
    """Normalize one Naver row → the snapshot we persist.

    Computes 괴리율 (premium/discount) deterministically: nowVal vs nav. NAV
    is the underlying basket value; nowVal is the market price. (nowVal − nav)
    / nav × 100 is the ETF's premium (positive) or discount (negative).
    """
    def _f(k: str) -> Optional[float]:
        v = item.get(k)
        if v is None or v == "":
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    now, nav = _f("nowVal"), _f("nav")
    premium_pct = (round((now - nav) / nav * 100.0, 3)
                   if now is not None and nav and nav > 0 else None)

    tab = item.get("etfTabCode")
    try:
        tab_i = int(tab) if tab is not None else None
    except (TypeError, ValueError):
        tab_i = None

    return {
        "code": item.get("itemcode"),
        "name": item.get("itemname"),                 # may be mojibake — universe gives canonical
        "now": now,
        "nav": nav,
        "premium_pct": premium_pct,                    # 괴리율 (+ premium / − discount)
        "change_pct": _f("changeRate"),
        "return_3m_pct": _f("threeMonthEarnRate"),
        "aum_eokwon": _f("marketSum"),                 # 시가총액(억원) ≈ 운용자산
        "volume": _f("quant"),
        "value_mwon": _f("amonut"),                    # 거래대금(백만원)
        "category": _TAB_LABEL.get(tab_i),
        "tab_code": tab_i,
        "source": "naver",
    }
