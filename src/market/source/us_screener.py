"""US conditional screener (KIS) — market data, not a broker function.

Toss has rankings (거래대금·거래량·등락률 상위) but no conditional screener:
there is no way to ask it for "NASDAQ, PER under 15, market cap over $2B".
KIS answers exactly that question, so the KIS account stays alive for this
one endpoint and nothing else. Orders, balances, quotes and 수급 all moved to
`broker/toss.py`; `broker/auth.py` survives only to sign these requests.

The rest of the old KIS trading code lives in archive/quant/broker/.

It sat under quant/broker/ while KIS was the broker. It is not an account
concern — "NASDAQ, PER under 15" is a question about the market — and leaving
it there meant the shared research layer had to import quant to ask it.
"""
from __future__ import annotations

from .kis_auth import call

# 조회용 거래소 코드는 (구) 주문용 코드와 다르다.
_SCREEN_EXCD = {"NASD": "NAS", "NYSE": "NYS", "AMEX": "AMS",
                "NASDAQ": "NAS", "NAS": "NAS", "NYS": "NYS", "AMS": "AMS"}

# our filter key -> (KIS CO_* stem, unit scale)
# KIS units: 시총·거래대금 = 천 USD. We take $M from the caller and scale.
_SCREEN_MAP = {
    "price": ("PRICECUR", 1),
    "rate": ("RATE", 1),
    "mcap_musd": ("VALX", 1000),      # $M -> 천USD
    "volume": ("VOLUME", 1),
    "value_musd": ("AMT", 1000),      # $M -> 천USD
    "eps": ("EPS", 1),
    "per": ("PER", 1),
}


def screen(exchange: str, filters: dict, limit: int = 30) -> dict:
    """해외주식조건검색 (HHDFS76410000). filters: {key_min/key_max: number}
    with keys price, rate, mcap_musd, volume, value_musd, eps, per."""
    excd = _SCREEN_EXCD.get(exchange.upper())
    if not excd:
        return {"error": f"exchange must be one of {sorted(set(_SCREEN_EXCD))}"}
    params = {"AUTH": "", "EXCD": excd, "KEYB": ""}
    used = {}
    for key, (stem, scale) in _SCREEN_MAP.items():
        lo, hi = filters.get(f"{key}_min"), filters.get(f"{key}_max")
        if lo is None and hi is None:
            continue
        used[key] = {"min": lo, "max": hi}
        params[f"CO_YN_{stem}"] = "1"
        # KIS wants both ends when the condition is on; use wide defaults.
        params[f"CO_ST_{stem}"] = str(int((lo if lo is not None else 0) * scale))
        params[f"CO_EN_{stem}"] = str(int((hi if hi is not None else 999999999) * scale))
    data = call("/uapi/overseas-price/v1/quotations/inquire-search",
                "HHDFS76410000", params=params)
    rows = None
    for k in ("output2", "output1", "output"):
        v = data.get(k)
        if isinstance(v, list) and v:
            rows = v
            break
    out = []
    for r in rows or []:
        sym = r.get("symb") or r.get("rsym", "")[-6:].strip()
        if not sym:
            continue
        out.append({
            "symbol_id": f"US:{sym}",
            "name": (r.get("name") or r.get("knam") or "").strip(),
            "price": float(r.get("last") or 0),
            "rate": float(r.get("rate") or 0),
            "volume": float(r.get("tvol") or 0),
            "mcap_musd": round(float(r.get("valx") or 0) / 1000, 1),
            "per": float(r.get("per") or 0) or None,
            "eps": float(r.get("eps") or 0) or None,
            "rank": float(r.get("rank") or 0),
        })
    return {"exchange": excd, "filters": used, "matched": len(out),
            "returned": min(len(out), limit), "results": out[:limit],
            "note": "단위: mcap_musd/value_musd=백만달러, KIS 조건검색은 "
                    "당일 시세 기준. PBR·배당 필터는 미지원(KR screen과 다름)."}
