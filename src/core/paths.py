"""Filesystem layout for the new portfolio-dashboard.

  data/portfolio.db                                 — SQLite (ledger: accounts, holdings, trades, cash, snapshots, plans + chat_sessions)
  data/market.db                                    — SQLite (ohlcv, valuation, financials, sector, etf, symbol_themes)
  data/news.db                                      — SQLite (signals, signal_themes, theme_daily/weekly/monthly)
  data/universe/{kr,us}.json                        — symbol master (daily refresh)
  data/market/quote/last.json                       — last-known quote snapshot
  data/universe/dart_corp_codes.json                — KR code → DART corp_code map
  data/analysis/analysis/{MARKET}_{CODE}/{YYYYMMDD}.json — comprehensive analysis results
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"

DB_PATH = DATA / "portfolio.db"

# The symbol book: bars, statements, valuation, sector, ETF facts and theme
# membership — everything true about a symbol regardless of who owns it. The
# account book reads it and does not own it. See src/market/store/db.py.
MARKET_DB_PATH = DATA / "market.db"

# The outside world: collected articles, their theme membership, and the
# daily/weekly/monthly summaries built on top of them. The only book that is
# not about what we own. See src/research/news/db.py.
NEWS_DB_PATH = DATA / "news.db"

UNIVERSE_DIR = DATA / "universe"

MARKET_DIR = DATA / "market"
QUOTE_DIR = MARKET_DIR / "quote"   # last.json only — the rest is market.db

ANALYSIS_DIR = DATA / "analysis"

LOGS_DIR = DATA / "logs"

# Working directory of the chat agent's CLI: kept empty, because the CLI lets
# its built-in read-only commands (head, cat, ls) read anything under the
# working directory. See research/llm_backend.py, tool lockdown.
CHAT_CWD = DATA / "chat_sandbox"
BACKUP_DIR = DATA / "backup"

# KR stock code (6-digit) → DART 8-digit corp_code mapping, cached once.
DART_CORP_CODES = UNIVERSE_DIR / "dart_corp_codes.json"


def ensure_dirs() -> None:
    for d in (DATA, UNIVERSE_DIR, MARKET_DIR, QUOTE_DIR,
              ANALYSIS_DIR, LOGS_DIR, BACKUP_DIR):
        d.mkdir(parents=True, exist_ok=True)


def analysis_path(kind: str, symbol_id: str, yyyymmdd: str) -> Path:
    safe = symbol_id.replace(":", "_")
    return ANALYSIS_DIR / kind / safe / f"{yyyymmdd}.json"


def split_symbol(symbol_id: str) -> tuple[str, str]:
    """'KR:005930' → ('KR', '005930'). New convention: market = KR | US."""
    market, code = symbol_id.split(":", 1)
    return market.upper(), code
