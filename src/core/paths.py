"""Filesystem layout for the new portfolio-dashboard.

  data/portfolio.db                                 — SQLite (accounts, holdings, balances, snapshots)
  data/universe/{kr,us,etf_kr}.json                 — symbol master (daily refresh)
  data/market/ohlcv/{MARKET}/{CODE}.parquet         — per-symbol daily bars
  data/market/quote/last.json                       — last-known quote snapshot
  data/market/valuation/{MARKET}/{CODE}.parquet     — daily valuation (PER/PBR/EPS/...)
  data/market/financials/{MARKET}/{CODE}.parquet    — quarterly/annual statements
  data/signals/news/{THEME}/{YYYYMM}.jsonl          — theme-keyed qualitative signals
  data/universe/dart_corp_codes.json                — KR code → DART corp_code map
  data/analysis/{kind}/{MARKET}_{CODE}/{YYYYMMDD}.json
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"

DB_PATH = DATA / "portfolio.db"

UNIVERSE_DIR = DATA / "universe"

MARKET_DIR = DATA / "market"
OHLCV_DIR = MARKET_DIR / "ohlcv"
QUOTE_DIR = MARKET_DIR / "quote"
VALUATION_DIR = MARKET_DIR / "valuation"
FINANCIALS_DIR = MARKET_DIR / "financials"
SECTOR_DIR = MARKET_DIR / "sector"
ETF_DIR = MARKET_DIR / "etf"

ANALYSIS_DIR = DATA / "analysis"

SIGNALS_DIR = DATA / "signals"          # qualitative raw signals (news/trend)

LOGS_DIR = DATA / "logs"
BACKUP_DIR = DATA / "backup"

# KR stock code (6-digit) → DART 8-digit corp_code mapping, cached once.
DART_CORP_CODES = UNIVERSE_DIR / "dart_corp_codes.json"


def ensure_dirs() -> None:
    for d in (DATA, UNIVERSE_DIR, MARKET_DIR, OHLCV_DIR, QUOTE_DIR,
              VALUATION_DIR, FINANCIALS_DIR, SECTOR_DIR, ETF_DIR,
              ANALYSIS_DIR, SIGNALS_DIR, LOGS_DIR, BACKUP_DIR):
        d.mkdir(parents=True, exist_ok=True)


def signals_news_path(theme: str, yyyymm: str) -> Path:
    """Raw qualitative signals per theme, monthly jsonl (L3 collect output)."""
    return SIGNALS_DIR / "news" / theme / f"{yyyymm}.jsonl"


def ohlcv_path(market: str, code: str) -> Path:
    return OHLCV_DIR / market.upper() / f"{code}.parquet"


def valuation_path(market: str, code: str) -> Path:
    return VALUATION_DIR / market.upper() / f"{code}.parquet"


def financials_path(market: str, code: str) -> Path:
    return FINANCIALS_DIR / market.upper() / f"{code}.parquet"


def sector_path(market: str, code: str) -> Path:
    """Per-symbol sector/industry tag cache (refreshed in the daily batch)."""
    return SECTOR_DIR / market.upper() / f"{code}.json"


def etf_path(market: str, code: str) -> Path:
    """Per-ETF daily snapshot (NAV/AUM/괴리율/3M return).
    Single file overwritten each day — history lives in OHLCV instead."""
    return ETF_DIR / market.upper() / f"{code}.json"


def analysis_path(kind: str, symbol_id: str, yyyymmdd: str) -> Path:
    safe = symbol_id.replace(":", "_")
    return ANALYSIS_DIR / kind / safe / f"{yyyymmdd}.json"


def analysis_news_daily_path(theme: str, date: str) -> Path:
    """Per-theme daily digest (L4). date = 'YYYY-MM-DD'."""
    return ANALYSIS_DIR / "news" / theme / "daily" / f"{date}.json"


def analysis_news_state_path(theme: str) -> Path:
    """Per-theme rolling state (L4 evaluation input). Always-current."""
    return ANALYSIS_DIR / "news" / theme / "state.json"


def split_symbol(symbol_id: str) -> tuple[str, str]:
    """'KR:005930' → ('KR', '005930'). New convention: market = KR | US."""
    market, code = symbol_id.split(":", 1)
    return market.upper(), code
