"""SEC EDGAR companyfacts — US financial statements with real history (L2).

yfinance quarterly caps out around 6 quarters; EDGAR's XBRL companyfacts API
carries the full filing history for free, which is what lets US symbols get
the same trend metrics (derived_series, YoY, valuation bands) KR gets from
DART.

Endpoints (SEC requires a descriptive User-Agent with a contact):
  https://www.sec.gov/files/company_tickers.json          ticker -> CIK
  https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json all facts

Period semantics differ from DART's columns: every XBRL fact carries its own
start/end, so quarter-vs-cumulative is decided per entry by duration —
~70-100 days is a quarter-only flow value, ~350-380 days an FY value.
Balance-sheet items are instantaneous (end only). Fiscal quarters that don't
align to calendar quarters (AVGO ends Feb/May/Aug/Nov) are labeled by the
calendar quarter of their period end — same convention the yfinance rows
already used, so stored series stay comparable.

Rate limits: SEC allows 10 req/s; we make at most 2 per symbol and cache the
ticker map for a week.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime
from typing import Dict, List, Optional

import requests

from .base import FinancialRow
from ...core.paths import UNIVERSE_DIR, split_symbol

log = logging.getLogger(__name__)

_UA = {"User-Agent": "stock-portfolio/1.0 (contact: admin@jsyoo.dev)"}
_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
_CIK_CACHE = UNIVERSE_DIR / "edgar_ciks.json"
_CIK_TTL_S = 7 * 86400

# standardized field -> us-gaap concepts, first hit wins
_CONCEPTS = {
    "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax",
                "Revenues", "SalesRevenueNet",
                "RevenueFromContractWithCustomerIncludingAssessedTax"),
    "gross_profit": ("GrossProfit",),
    "operating_income": ("OperatingIncomeLoss",),
    "net_income": ("NetIncomeLoss",),
    "eps": ("EarningsPerShareBasic",),
    "total_assets": ("Assets",),
    "total_liabilities": ("Liabilities",),
    "total_equity": ("StockholdersEquity",
                     "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
    "current_assets": ("AssetsCurrent",),
    "current_liabilities": ("LiabilitiesCurrent",),
    "operating_cf": ("NetCashProvidedByUsedInOperatingActivities",),
    "investing_cf": ("NetCashProvidedByUsedInInvestingActivities",),
    "financing_cf": ("NetCashProvidedByUsedInFinancingActivities",),
}
_INSTANT = {"total_assets", "total_liabilities", "total_equity",
            "current_assets", "current_liabilities"}


def _cik_map() -> Dict[str, int]:
    try:
        cached = json.loads(_CIK_CACHE.read_text(encoding="utf-8"))
        if time.time() - cached.get("_fetched", 0) < _CIK_TTL_S:
            return {k: int(v) for k, v in cached["map"].items()}
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        pass
    res = requests.get(_TICKERS_URL, headers=_UA, timeout=20)
    res.raise_for_status()
    m = {row["ticker"].upper(): int(row["cik_str"])
         for row in res.json().values()}
    _CIK_CACHE.parent.mkdir(parents=True, exist_ok=True)
    _CIK_CACHE.write_text(json.dumps({"_fetched": time.time(), "map": m}),
                          encoding="utf-8")
    return m


def cik_for(symbol_id: str) -> Optional[int]:
    _, ticker = split_symbol(symbol_id)
    return _cik_map().get(ticker.upper())


def _q_label(end: date) -> str:
    return f"{end.year}Q{(end.month - 1) // 3 + 1}"


def _iter_entries(facts: dict, concepts: tuple) -> list[dict]:
    gaap = facts.get("facts", {}).get("us-gaap", {})
    for concept in concepts:
        node = gaap.get(concept)
        if not node:
            continue
        units = node.get("units", {})
        for unit in ("USD", "USD/shares"):
            if unit in units:
                return units[unit]
    return []


def get_financials(symbol_id: str, years: int = 6) -> List[FinancialRow]:
    """Quarter-only rows plus FY rows, newest ~`years` years. Empty list on
    any failure — the caller falls back to yfinance."""
    cik = cik_for(symbol_id)
    if cik is None:
        log.warning("edgar: no CIK for %s", symbol_id)
        return []
    try:
        res = requests.get(_FACTS_URL.format(cik=cik), headers=_UA, timeout=30)
        res.raise_for_status()
        facts = res.json()
    except (requests.RequestException, ValueError) as e:
        log.warning("edgar %s companyfacts failed: %s", symbol_id, e)
        return []
    return rows_from_facts(facts, years=years)


def latest_filed(facts: dict) -> Optional[str]:
    """Newest `filed` date anywhere in a companyfacts blob — what the symbol's
    financial rows should be stamped with."""
    return max((e.get("filed") or "" for concepts in _CONCEPTS.values()
                for e in _iter_entries(facts, concepts)), default="") or None


def rows_from_facts(facts: dict, years: int = 6) -> List[FinancialRow]:
    """Parse one companyfacts blob into rows.

    Split out of get_financials so the bulk path can feed it members of
    companyfacts.zip — 7,069 symbols in one download instead of one request
    each — without a second copy of this mapping.
    """
    today = date.today()
    cutoff = today.replace(year=today.year - years)
    # (period_label, field) -> (filed, value); latest filing wins (restatements)
    cells: Dict[tuple, tuple] = {}
    ends: Dict[str, date] = {}

    for field, concepts in _CONCEPTS.items():
        for e in _iter_entries(facts, concepts):
            if e.get("form") not in ("10-Q", "10-K", "20-F", "40-F"):
                continue
            try:
                end = datetime.strptime(e["end"], "%Y-%m-%d").date()
                val = float(e["val"])
            except (KeyError, TypeError, ValueError):
                continue
            if end < cutoff:
                continue
            filed = e.get("filed") or ""
            if field in _INSTANT:
                label = _q_label(end)
            else:
                try:
                    start = datetime.strptime(e["start"], "%Y-%m-%d").date()
                except (KeyError, TypeError, ValueError):
                    continue
                days = (end - start).days
                if 70 <= days <= 100:
                    label = _q_label(end)
                elif 350 <= days <= 380:
                    label = f"{end.year}FY"
                else:
                    continue        # H1/9M cumulatives — skip, quarters suffice
            key = (label, field)
            if key not in cells or filed > cells[key][0]:
                cells[key] = (filed, val)
            ends.setdefault(label, end)

    periods = sorted({label for label, _ in cells},
                     key=lambda p: (ends.get(p, date.min), p.endswith("FY")))
    rows: List[FinancialRow] = []
    for label in periods:
        vals = {f: cells[(label, f)][1] for f in _CONCEPTS
                if (label, f) in cells}
        if "net_income" not in vals and "revenue" not in vals:
            continue                # a period with only stray BS facts
        filed = max((cells[(label, f)][0] for f in _CONCEPTS
                     if (label, f) in cells), default="") or None
        rows.append(FinancialRow(
            period=label, fs_div="CFS",
            report_date=str(ends.get(label, "")), source="edgar",
            filed_at=filed, **vals))
    return rows
