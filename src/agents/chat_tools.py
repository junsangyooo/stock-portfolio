"""Read-only lookup CLI for the portfolio chat agent.

The chat backend (`src/api/chat.py`) runs `claude -p` with exactly one
allowed command family:

    .venv/bin/python -m src.agents.chat_tools <command> [arg ...]

Commands call the same internal read functions the API routers use — no
HTTP, no session, and by construction no writes. One JSON object goes to
stdout; failures come back as {"error": ...} with exit 0 so the model can
read and recover from them.

Commands:
    search <query>              universe fuzzy search (KR+US, top 8)
    quote <SYMBOL_ID>           latest price/prev-close (KR:005930 | US:AAPL)
    chart <SYMBOL_ID>           live chart facts (indicators, no LLM)
    fundamental <SYMBOL_ID>     valuation + financials facts (trimmed)
    verdict <SYMBOL_ID> [kind]  latest persisted verdict (default: judge)
    news <SYMBOL_ID>            connected news signals (themes, developments)
"""
from __future__ import annotations

import json
import sys


def _split(symbol_id: str) -> tuple[str, str]:
    sid = symbol_id.strip().upper()
    if ":" not in sid:
        raise ValueError(f"symbol_id must be KR:code or US:ticker, got {sid!r}")
    market, code = sid.split(":", 1)
    return market, code


def _cmd_search(query: str) -> dict:
    from ..universe import search as uni_search
    rows = uni_search.search(query, limit=8)
    return {"query": query, "results": [
        {"symbol_id": r.get("symbol_id"), "name_kr": r.get("name_kr"),
         "name_en": r.get("name_en"), "market": r.get("market"),
         "is_etf": r.get("is_etf")} for r in rows]}


def _cmd_quote(symbol_id: str) -> dict:
    from ..api.market import get_quotes
    _split(symbol_id)  # validate format
    return get_quotes(symbols=symbol_id.strip().upper())


def _cmd_chart(symbol_id: str) -> dict:
    from ..api.analysis import get_analysis
    market, code = _split(symbol_id)
    return get_analysis("chart", market, code)


def _cmd_fundamental(symbol_id: str) -> dict:
    from ..api.analysis import get_fundamental_facts
    market, code = _split(symbol_id)
    facts = get_fundamental_facts(market, code)
    series = facts.get("financials_series")
    if isinstance(series, list) and len(series) > 4:
        facts["financials_series"] = series[-4:]
        facts["financials_series_note"] = f"최근 4분기만 표시 (전체 {len(series)}분기)"
    return facts


def _cmd_verdict(symbol_id: str, kind: str = "judge") -> dict:
    from ..api.analysis import get_verdict
    market, code = _split(symbol_id)
    return get_verdict(kind, market, code)


def _cmd_news(symbol_id: str) -> dict:
    from ..api.analysis import news_signals
    market, code = _split(symbol_id)
    return news_signals(market, code)


_COMMANDS = {
    "search": _cmd_search,
    "quote": _cmd_quote,
    "chart": _cmd_chart,
    "fundamental": _cmd_fundamental,
    "verdict": _cmd_verdict,
    "news": _cmd_news,
}


def main(argv: list[str]) -> int:
    try:
        if len(argv) < 2 or argv[0] not in _COMMANDS:
            out = {"error": "usage: chat_tools <command> <arg>",
                   "commands": sorted(_COMMANDS)}
        else:
            out = _COMMANDS[argv[0]](*argv[1:])
    except Exception as e:  # noqa: BLE001 — the model reads the error and retries
        out = {"error": f"{type(e).__name__}: {e}"[:400]}
    print(json.dumps(out, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
