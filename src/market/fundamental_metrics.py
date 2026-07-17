"""L3 derived fundamental metrics — pure functions, computed on the fly.

Per the design (D5), derived ratios are NOT stored; they are recomputed from
L2 financials + L1 valuation whenever requested, so they never diverge from
their inputs.

All functions are pandas-free and None-safe: any missing / zero-denominator
input yields None instead of raising. This keeps the module trivially
unit-testable without the data stack installed.

Korean convention (D7): debt_ratio = total_liabilities / total_equity * 100.
"""
from __future__ import annotations

from typing import List, Optional

Num = Optional[float]


def _ratio(numer: Num, denom: Num, *, pct: bool = False) -> Num:
    """numer/denom, guarding None and zero denominator. pct=True → *100."""
    if numer is None or denom is None:
        return None
    try:
        if denom == 0:
            return None
        r = numer / denom
        return r * 100 if pct else r
    except (TypeError, ZeroDivisionError):
        return None


# ── profitability ────────────────────────────────────────────────────────
def operating_margin(operating_income: Num, revenue: Num) -> Num:
    return _ratio(operating_income, revenue)


def net_margin(net_income: Num, revenue: Num) -> Num:
    return _ratio(net_income, revenue)


def roe(net_income: Num, total_equity: Num) -> Num:
    return _ratio(net_income, total_equity)


def roa(net_income: Num, total_assets: Num) -> Num:
    return _ratio(net_income, total_assets)


# ── stability ────────────────────────────────────────────────────────────
def debt_ratio(total_liabilities: Num, total_equity: Num) -> Num:
    """Korean 부채비율 = 부채총계 / 자본총계 * 100 (D7)."""
    return _ratio(total_liabilities, total_equity, pct=True)


def current_ratio(current_assets: Num, current_liabilities: Num) -> Num:
    return _ratio(current_assets, current_liabilities, pct=True)


# ── growth ───────────────────────────────────────────────────────────────
def yoy(current: Num, prior: Num) -> Num:
    """Year-over-year growth rate. prior must be the same quarter a year ago.

    Returns a fraction (0.12 = +12%). Guards sign flips by using abs(prior)
    in the denominator so a swing from negative→positive reads as positive
    growth rather than a misleading negative.
    """
    if current is None or prior is None or prior == 0:
        return None
    return (current - prior) / abs(prior)


def compute(latest: dict, prior_year: Optional[dict] = None,
            valuation: Optional[dict] = None) -> dict:
    """Compute the full L3 metric bundle for one symbol.

    latest       — most-recent FinancialRow as dict (L2)
    prior_year   — same-quarter-last-year FinancialRow as dict, for YoY
    valuation    — latest ValuationPoint as dict (L1), passed through

    Returns a flat dict of derived ratios (+ passthrough valuation) with
    None for anything that cannot be computed.
    """
    latest = latest or {}
    prior_year = prior_year or {}
    valuation = valuation or {}

    rev = latest.get("revenue")
    out: dict = {
        "operating_margin": operating_margin(latest.get("operating_income"), rev),
        "net_margin": net_margin(latest.get("net_income"), rev),
        "roe": roe(latest.get("net_income"), latest.get("total_equity")),
        "roa": roa(latest.get("net_income"), latest.get("total_assets")),
        "debt_ratio": debt_ratio(latest.get("total_liabilities"),
                                 latest.get("total_equity")),
        "current_ratio": current_ratio(latest.get("current_assets"),
                                        latest.get("current_liabilities")),
        "revenue_yoy": yoy(rev, prior_year.get("revenue")),
        "operating_income_yoy": yoy(latest.get("operating_income"),
                                    prior_year.get("operating_income")),
    }
    # passthrough L1 valuation (no transform — already terminal)
    for k in ("per", "pbr", "psr", "eps", "bps", "dps", "div_yield",
              "market_cap"):
        out[k] = valuation.get(k)
    return out


# ── input-data sanity guard ────────────────────────────────────────────────
# Plausibility bounds for derived ratios (fractions unless noted). These are
# deliberately generous — verified-real extremes must pass (Samsung 2026Q1
# operating margin 0.4275 is a real filing), so a flag means "verify this
# symbol's stored financials against the filing", never a hard failure.
_ANOMALY_BOUNDS = {
    "operating_margin": (-1.0, 0.6),
    "net_margin": (-1.0, 0.8),
    "roe": (-1.0, 1.0),
    "roa": (-0.5, 0.5),
    "revenue_yoy": (-0.95, 3.0),
}
_QUALITY_BOUNDS = {
    "qoe": (-10.0, 10.0),
    "sloan_accrual": (-1.0, 1.0),
}


def anomaly_flags(metrics: dict, quality: Optional[dict] = None) -> List[str]:
    """Warning strings for implausible derived values (likely bad L2 inputs).
    None-safe; empty list = nothing suspicious. Callers log, never fail."""
    flags: List[str] = []

    def _check(d, bounds):
        for key, (lo, hi) in bounds.items():
            v = (d or {}).get(key)
            if isinstance(v, (int, float)) and not (lo <= v <= hi):
                flags.append(f"{key}={v:.4g} outside plausible [{lo}, {hi}]")

    _check(metrics, _ANOMALY_BOUNDS)
    _check(quality, _QUALITY_BOUNDS)
    return flags


# ── earnings quality ───────────────────────────────────────────────────────
def quality_of_earnings(operating_cf: Num, net_income: Num) -> Num:
    """QoE = operating_cf / net_income (rulebook §B5). ≥1 = cash-backed."""
    return _ratio(operating_cf, net_income)


def sloan_accrual(net_income: Num, operating_cf: Num, investing_cf: Num,
                  total_assets: Num) -> Num:
    """Sloan accrual ratio (rulebook §B6):
        (net_income − operating_cf − investing_cf) / total_assets
    Safe band ±10%; outside ±25% = non-cash earnings risk."""
    if net_income is None or operating_cf is None or investing_cf is None:
        return None
    if not total_assets:
        return None
    return (net_income - operating_cf - investing_cf) / total_assets


# ── Altman Z-Score (approx — rulebook §C4) ──────────────────────────────────
def altman_z(latest: dict, market_cap: Num) -> dict:
    """Approximate Altman Z. X2 (retained earnings) is unavailable, so we
    substitute total_equity/total_assets (rulebook §C4/§G note). Returns
    {z, zone, components, approx}; z/zone None if any component is missing."""
    latest = latest or {}
    ta = latest.get("total_assets")
    tl = latest.get("total_liabilities")
    ca = latest.get("current_assets")
    cl = latest.get("current_liabilities")
    te = latest.get("total_equity")
    oi = latest.get("operating_income")
    rev = latest.get("revenue")

    none = {"z": None, "zone": None, "components": {}, "approx": True}
    if not ta:
        return none
    x1 = (ca - cl) / ta if (ca is not None and cl is not None) else None
    x2 = te / ta if te is not None else None       # retained-earnings proxy
    x3 = oi / ta if oi is not None else None        # EBIT proxy
    x4 = market_cap / tl if (market_cap is not None and tl) else None
    x5 = rev / ta if rev is not None else None
    comps = {"x1": x1, "x2": x2, "x3": x3, "x4": x4, "x5": x5}
    if any(v is None for v in comps.values()):
        return {"z": None, "zone": None, "components": comps, "approx": True}

    z = 1.2 * x1 + 1.4 * x2 + 3.3 * x3 + 0.6 * x4 + 1.0 * x5
    zone = "safe" if z > 3.0 else ("grey" if z >= 1.8 else "distress")
    return {"z": round(z, 2), "zone": zone,
            "components": {k: round(v, 4) for k, v in comps.items()},
            "approx": True}


# ── Piotroski F-Score (partial 8/9 — rulebook §C2/§G) ───────────────────────
def piotroski_f(latest: dict, prior_year: Optional[dict]) -> dict:
    """Piotroski F-Score using 8 of 9 signals (the share-issuance test #7 is
    dropped — Δshares unavailable, §G). Each computable signal scores 1.
    `max` counts only the signals we could evaluate; uncomputable ones are
    reported as None so confidence can be lowered downstream."""
    latest = latest or {}
    p = prior_year or {}

    roa_now = roa(latest.get("net_income"), latest.get("total_assets"))
    roa_prev = roa(p.get("net_income"), p.get("total_assets"))
    ocf = latest.get("operating_cf")
    ta = latest.get("total_assets")
    accrual_ok = None
    if ocf is not None and ta and roa_now is not None:
        accrual_ok = (ocf / ta) > roa_now           # OCF-funded earnings

    dr_now = debt_ratio(latest.get("total_liabilities"), latest.get("total_equity"))
    dr_prev = debt_ratio(p.get("total_liabilities"), p.get("total_equity"))
    cr_now = current_ratio(latest.get("current_assets"), latest.get("current_liabilities"))
    cr_prev = current_ratio(p.get("current_assets"), p.get("current_liabilities"))

    gm_now = _ratio(latest.get("gross_profit"), latest.get("revenue"))
    gm_prev = _ratio(p.get("gross_profit"), p.get("revenue"))
    at_now = _ratio(latest.get("revenue"), latest.get("total_assets"))
    at_prev = _ratio(p.get("revenue"), p.get("total_assets"))

    def _gt0(v):
        return None if v is None else v > 0

    def _delta_pos(now, prev):
        return None if (now is None or prev is None) else now > prev

    def _delta_neg(now, prev):
        return None if (now is None or prev is None) else now < prev

    comps = {
        # profitability (4)
        "roa_pos": _gt0(roa_now),
        "ocf_pos": _gt0(ocf),
        "droa_pos": _delta_pos(roa_now, roa_prev),
        "accrual_ok": accrual_ok,
        # leverage / liquidity (2 — share issuance #7 dropped)
        "leverage_down": _delta_neg(dr_now, dr_prev),
        "liquidity_up": _delta_pos(cr_now, cr_prev),
        # efficiency (2)
        "margin_up": _delta_pos(gm_now, gm_prev),
        "turnover_up": _delta_pos(at_now, at_prev),
    }
    evaluable = {k: v for k, v in comps.items() if v is not None}
    score = sum(1 for v in evaluable.values() if v)
    return {"score": score, "max": len(comps), "evaluated": len(evaluable),
            "components": comps}


# ── trend series + valuation band helpers ──────────────────────────────────
_TREND_KEYS = ("operating_margin", "net_margin", "roe", "roa", "debt_ratio",
               "current_ratio", "revenue_yoy", "operating_income_yoy")


def derived_series(rows: List[dict], last_n: int = 8) -> List[dict]:
    """Per-period derived metrics for the most recent `last_n` periods, so the
    agent can read trends (margin/ROE/debt drift) the rulebook keeps demanding.
    YoY uses the matching prior-year row found within `rows`."""
    rows = rows or []
    out: List[dict] = []
    for r in rows[-last_n:]:
        prior = find_prior_year(rows, r.get("period", ""))
        d = compute(r, prior_year=prior, valuation=None)
        out.append({"period": r.get("period"),
                    **{k: d.get(k) for k in _TREND_KEYS}})
    return out


def percentile_rank(values, current: Num) -> Num:
    """Fraction of `values` ≤ `current` (0..1) — the symbol's own valuation
    band position. None if fewer than 5 usable points or `current` missing."""
    if current is None:
        return None
    vals = [v for v in (values or []) if v is not None]
    if len(vals) < 5:
        return None
    below = sum(1 for v in vals if v <= current)
    return round(below / len(vals), 3)


def find_prior_year(rows: List[dict], latest_period: str) -> Optional[dict]:
    """Given period rows and a 'YYYYQn'/'YYYYFY' label, return the matching
    period one year earlier, or None. Pure string arithmetic on the label."""
    if not latest_period or len(latest_period) < 5:
        return None
    try:
        year = int(latest_period[:4])
    except ValueError:
        return None
    suffix = latest_period[4:]            # "Q3" | "FY"
    target = f"{year - 1}{suffix}"
    for r in rows:
        if r.get("period") == target:
            return r
    return None
