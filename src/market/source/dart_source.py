"""DART (OpenDART) adapter — KR financial statements (L2).

Standalone (not a Source subclass): DART has no OHLCV/quote, only statements.
The fundamental pipeline calls this directly for KR symbols.

Pipeline:
  1. resolve KR 6-digit stock code → DART 8-digit corp_code (cached once)
  2. finstate_all(corp_code, year, reprt_code, fs_div='CFS')  [fnlttSinglAcntAll]
  3. parse Korean account names → standardized FinancialRow fields

reprt_code → period label:
  11013 → Q1   (1분기보고서)
  11012 → Q2   (반기보고서)
  11014 → Q3   (3분기보고서)
  11011 → FY   (사업보고서, annual)

Interim-report column semantics (verified against 2025-2026 filings of
005930/035420/005070, 2026-07):
  - IS/CIS: thstrm_amount = 3-month (당분기) figure; thstrm_add_amount = YTD.
    → quarterly rows are quarter-only as parsed, no conversion needed.
  - CF: the statement is only presented cumulatively in interim reports —
    thstrm_amount = YTD, thstrm_add_amount = None.
    → derive_quarter_cf() converts Q2/Q3 CF to quarter-only by differencing
      same-year cumulatives, so CF lines up with the 3-month IS figures.
  - BS: point-in-time, no conversion.

API key: env DART_API_KEY (or DART_PERSONAL_API_KEY); falls back to parsing
~/.claude/secrets/.env for local CLI runs (systemd injects it on Oracle).
"""
from __future__ import annotations

import json
from datetime import date
from typing import Dict, Iterable, List, Optional

from .base import FinancialRow
from ...core import secrets
from ...core.paths import DART_CORP_CODES, split_symbol

# reprt_code → (period suffix, ordered list for "latest N")
REPRT = {"11013": "Q1", "11012": "Q2", "11014": "Q3", "11011": "FY"}
REPRT_ORDER = ["11013", "11012", "11014", "11011"]

# Korean account-name patterns per standardized field (matched within sj_div).
# (sj_div set, field, [substrings — first match wins, in order])
_ACCOUNT_RULES = [
    ({"IS", "CIS"}, "revenue", ["매출액", "영업수익", "수익(매출"]),
    ({"IS", "CIS"}, "gross_profit", ["매출총이익"]),
    ({"IS", "CIS"}, "operating_income", ["영업이익"]),
    # 분기/반기 보고서는 '분기순이익'/'반기순이익', 사업보고서는 '당기순이익'.
    # '지배기업 소유주지분'은 귀속분이라 제외 (전체 순이익을 우선).
    ({"IS", "CIS"}, "net_income", ["분기순이익", "반기순이익", "당기순이익"]),
    # 기본주당이익(손실) → EPS. 희석('희석주당이익')은 제외. KR 밸류에이션 산출용.
    ({"IS"}, "eps", ["기본주당이익"]),
    ({"BS"}, "total_assets", ["자산총계"]),
    ({"BS"}, "total_liabilities", ["부채총계"]),
    ({"BS"}, "total_equity", ["자본총계"]),
    ({"BS"}, "current_assets", ["유동자산"]),
    ({"BS"}, "current_liabilities", ["유동부채"]),
    ({"CF"}, "operating_cf", ["영업활동"]),
    ({"CF"}, "investing_cf", ["투자활동"]),
    ({"CF"}, "financing_cf", ["재무활동"]),
]


def _api_key() -> Optional[str]:
    return secrets.get("DART_API_KEY") or secrets.get("DART_PERSONAL_API_KEY")


def _parse_amount(raw) -> Optional[float]:
    """'1,234,567' → 1234567.0; '-' / '' / None → None."""
    if raw is None:
        return None
    s = str(raw).strip().replace(",", "")
    if s in ("", "-", "nan"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def match_accounts(entries: Iterable) -> Dict[str, float]:
    """Run _ACCOUNT_RULES over one statement's rows (dict-likes carrying
    sj_div / account_nm / thstrm_amount) → {field: amount}. Shared by the
    per-symbol finstate_all parse and the bulk fnlttMultiAcnt backfill."""
    fields: Dict[str, float] = {}
    for r in entries:
        sj = str(r.get("sj_div") or "").strip()
        name = str(r.get("account_nm") or "").strip()
        amount = _parse_amount(r.get("thstrm_amount"))
        if amount is None:
            continue
        for sj_set, field, subs in _ACCOUNT_RULES:
            if field in fields or sj not in sj_set:
                continue
            if any(sub in name for sub in subs):
                fields[field] = amount
    return fields


_CF_FIELDS = ("operating_cf", "investing_cf", "financing_cf")


def derive_quarter_cf(rows: List[FinancialRow]) -> None:
    """Convert interim-report CF from cumulative (as filed) to quarter-only.

    Q2 = H1 − Q1, Q3 = 9M − H1, differencing raw cumulatives of the SAME year
    and SAME fs_div (a CFS/OFS mix must not be differenced). When the prior
    cumulative is unavailable the field becomes None — a cumulative left in a
    quarterly row would silently inflate QoE/Sloan/Piotroski downstream.
    Q1 (cum == quarter) and FY (annual vs annual IS) are untouched.
    Mutates rows in place."""
    by_year: Dict[str, Dict[str, FinancialRow]] = {}
    for r in rows:
        p = r.period or ""
        if len(p) >= 6:
            by_year.setdefault(p[:4], {})[p[4:]] = r
    for periods in by_year.values():
        # raw cumulatives captured before any mutation
        raw = {sfx: {f: getattr(row, f) for f in _CF_FIELDS}
               for sfx, row in periods.items()}
        for sfx, prior_sfx in (("Q2", "Q1"), ("Q3", "Q2")):
            row = periods.get(sfx)
            if row is None:
                continue
            prior = periods.get(prior_sfx)
            same_base = prior is not None and prior.fs_div == row.fs_div
            for f in _CF_FIELDS:
                cum = raw[sfx][f]
                prev = raw[prior_sfx][f] if same_base else None
                setattr(row, f,
                        cum - prev if (cum is not None and prev is not None)
                        else None)


def _pick_common_dps(records) -> Optional[float]:
    """Common-stock cash DPS from alotMatter rows (se/stock_knd/thstrm dicts).
    '주당 현금배당금' + stock_knd '보통주…' (uncommitted '-' → None)."""
    for r in records or []:
        se = str(r.get("se") or "")
        knd = str(r.get("stock_knd") or "")
        if "주당 현금배당금" in se and knd.startswith("보통주"):
            return _parse_amount(r.get("thstrm"))
    return None


class DartSource:
    name = "dart"

    def __init__(self) -> None:
        self._dart = None
        self._corp_map: Optional[Dict[str, str]] = None

    # ── lazy client ────────────────────────────────────────────────
    def _client(self):
        if self._dart is None:
            try:
                from opendartreader import OpenDartReader  # v0.3+
            except ImportError:  # pragma: no cover - legacy fallback
                import OpenDartReader  # type: ignore
            key = _api_key()
            if not key:
                raise RuntimeError("DART API key not found (env or ~/.claude/secrets/.env)")
            self._dart = OpenDartReader(key)
        return self._dart

    # ── corp_code resolution ───────────────────────────────────────
    def _load_corp_map(self) -> Dict[str, str]:
        if self._corp_map is not None:
            return self._corp_map
        if DART_CORP_CODES.exists():
            try:
                self._corp_map = json.loads(DART_CORP_CODES.read_text())
                return self._corp_map
            except Exception:
                pass
        self._corp_map = self._build_corp_map()
        return self._corp_map

    def _build_corp_map(self) -> Dict[str, str]:
        """Download CORPCODE.xml via OpenDartReader and cache stock→corp map."""
        df = self._client().corp_codes
        mapping: Dict[str, str] = {}
        for _, row in df.iterrows():
            stock = str(row.get("stock_code") or "").strip()
            corp = str(row.get("corp_code") or "").strip()
            if stock and stock not in ("", "nan") and corp:
                mapping[stock.zfill(6)] = corp
        DART_CORP_CODES.parent.mkdir(parents=True, exist_ok=True)
        DART_CORP_CODES.write_text(json.dumps(mapping, ensure_ascii=False))
        self._corp_map = mapping
        return mapping

    def corp_code(self, code: str) -> Optional[str]:
        return self._load_corp_map().get(code.zfill(6))

    # ── financials ─────────────────────────────────────────────────
    def get_financials(self, symbol_id: str, years: int = 4) -> List[FinancialRow]:
        """Fetch up to `years` of annual + quarterly statements for a KR symbol.

        OpenDartReader.finstate_all() resolves a 6-digit stock code → corp_code
        internally (and caches), so we pass the code directly."""
        market, code = split_symbol(symbol_id)
        if market != "KR":
            return []
        this_year = date.today().year
        rows: List[FinancialRow] = []
        for year in range(this_year, this_year - years - 1, -1):
            for reprt in REPRT_ORDER:
                row = self._fetch_one(code, year, reprt)
                if row is not None:
                    rows.append(row)
        derive_quarter_cf(rows)   # interim CF is filed cumulatively
        return rows

    def get_dps(self, symbol_id: str) -> Optional[Dict]:
        """Latest common-stock cash DPS from the annual dividend disclosure
        (alotMatter, via OpenDartReader.report(code, '배당', year)). Tries the
        last 3 completed fiscal years; returns {'dps': float, 'year': int}
        or None."""
        market, code = split_symbol(symbol_id)
        if market != "KR":
            return None
        last_fy = date.today().year - 1
        for year in range(last_fy, last_fy - 3, -1):
            try:
                df = self._client().report(code, "배당", year,
                                           reprt_code="11011")
            except Exception:
                df = None
            if df is None or len(df) == 0:
                continue
            dps = _pick_common_dps(r for _, r in df.iterrows())
            if dps is not None:
                return {"dps": dps, "year": year}
        return None

    def _fetch_one(self, code: str, year: int,
                   reprt: str) -> Optional[FinancialRow]:
        """Try consolidated (CFS) first; fall back to separate (OFS).

        Companies with no subsidiaries file ONLY 개별재무제표 (OFS) — for them
        CFS returns empty, so without this fallback they yield no financials.
        """
        for fs in ("CFS", "OFS"):
            try:
                df = self._client().finstate_all(code, year, reprt_code=reprt,
                                                 fs_div=fs)
            except Exception:
                df = None
            if df is not None and len(df) > 0:
                return self._parse(df, year, reprt, fs)
        return None

    def _parse(self, df, year: int, reprt: str,
               fs_div: str = "CFS") -> Optional[FinancialRow]:
        """Map a finstate_all DataFrame → one FinancialRow via account names."""
        fields = match_accounts(r for _, r in df.iterrows())
        if not fields:
            return None
        return FinancialRow(
            period=f"{year}{REPRT[reprt]}", fs_div=fs_div,
            report_date=f"{year}-12-31" if reprt == "11011" else "",
            source="dart", **fields,
        )
