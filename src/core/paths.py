"""Filesystem layout for the new portfolio-dashboard.

  data/portfolio.db                                 — SQLite (accounts, holdings, balances, snapshots)
  data/market.db                                    — SQLite (ohlcv, valuation, financials, sector, etf, symbol_themes)
  data/universe/{kr,us,etf_kr}.json                 — symbol master (daily refresh)
  data/market/quote/last.json                       — last-known quote snapshot
  data/signals/news/{THEME}/{YYYYMM}.jsonl          — theme-keyed qualitative signals
  data/universe/dart_corp_codes.json                — KR code → DART corp_code map
  data/analysis/{kind}/{MARKET}_{CODE}/{YYYYMMDD}.json
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"

DB_PATH = DATA / "portfolio.db"

# The auto-trading account lives in its own file. Pipelines and news are shared
# (they are symbol-keyed files under data/, not SQLite), but the account side is
# operated separately: a different schema, a different growth profile — a hot
# append-only journal has no business sharing a file with a small static record
# set — and a migration on one cannot reach the other.
QUANT_DB_PATH = DATA / "quant.db"

# The symbol book: bars, statements, valuation, sector, ETF facts and theme
# membership — everything true about a symbol regardless of who owns it. Both
# account books read it, neither owns it. See src/market/store/db.py.
MARKET_DB_PATH = DATA / "market.db"

UNIVERSE_DIR = DATA / "universe"

MARKET_DIR = DATA / "market"
QUOTE_DIR = MARKET_DIR / "quote"   # last.json only — the rest is market.db

ANALYSIS_DIR = DATA / "analysis"

SIGNALS_DIR = DATA / "signals"          # qualitative raw signals (news/trend)

LOGS_DIR = DATA / "logs"
BACKUP_DIR = DATA / "backup"

# KR stock code (6-digit) → DART 8-digit corp_code mapping, cached once.
DART_CORP_CODES = UNIVERSE_DIR / "dart_corp_codes.json"

QUANT_DIR = DATA / "quant"          # auto-trade runtime state (token cache, lock)


def ensure_dirs() -> None:
    for d in (DATA, UNIVERSE_DIR, MARKET_DIR, QUOTE_DIR,
              ANALYSIS_DIR, SIGNALS_DIR, LOGS_DIR, BACKUP_DIR, QUANT_DIR):
        d.mkdir(parents=True, exist_ok=True)


def signals_news_path(theme: str, yyyymm: str) -> Path:
    """Raw qualitative signals per theme, monthly jsonl (L3 collect output)."""
    return SIGNALS_DIR / "news" / theme / f"{yyyymm}.jsonl"


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
