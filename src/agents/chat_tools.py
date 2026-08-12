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
    screen k=v ...              KR whole-market factor screen (pykrx daily)
                                keys: per_min per_max pbr_min pbr_max div_min
                                      eps_min mcap_min mcap_max value_min
                                      market=KOSPI|KOSDAQ sort limit
                                (억원 단위: mcap_*, value_min. div=배당수익률 %)
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


_SCREEN_NUM_KEYS = {"per_min", "per_max", "pbr_min", "pbr_max", "div_min",
                    "eps_min", "mcap_min", "mcap_max", "value_min", "limit"}
_SCREEN_SORTS = {  # key -> (column, ascending)
    "per": ("per", True), "pbr": ("pbr", True), "div": ("div", False),
    "mcap": ("mcap_eok", False), "value": ("value_eok", False),
}


def _cmd_screen(*args: str) -> dict:
    filters: dict = {}
    for a in args:
        k, sep, v = a.partition("=")
        if not sep:
            raise ValueError(f"screen args must be key=value, got {a!r}")
        if k in _SCREEN_NUM_KEYS:
            filters[k] = float(v)
        elif k == "market":
            filters[k] = v.upper()
        elif k == "sort":
            if v not in _SCREEN_SORTS:
                raise ValueError(f"sort must be one of {sorted(_SCREEN_SORTS)}")
            filters[k] = v
        else:
            raise ValueError(f"unknown screen key {k!r}")

    from ..market.source import krx_screener
    from ..universe import search as uni_search
    date, df = krx_screener.load()

    # PER/PBR upper bounds imply >0 — pykrx reports 0 for 적자/미산출.
    if "per_max" in filters:
        df = df[(df["per"] > 0) & (df["per"] <= filters["per_max"])]
    if "per_min" in filters:
        df = df[df["per"] >= filters["per_min"]]
    if "pbr_max" in filters:
        df = df[(df["pbr"] > 0) & (df["pbr"] <= filters["pbr_max"])]
    if "pbr_min" in filters:
        df = df[df["pbr"] >= filters["pbr_min"]]
    if "div_min" in filters:
        df = df[df["div"] >= filters["div_min"]]
    if "eps_min" in filters:
        df = df[df["eps"] >= filters["eps_min"]]
    if "mcap_min" in filters:
        df = df[df["mcap_eok"] >= filters["mcap_min"]]
    if "mcap_max" in filters:
        df = df[df["mcap_eok"] <= filters["mcap_max"]]
    if "value_min" in filters:
        df = df[df["value_eok"] >= filters["value_min"]]

    sort_col, asc = _SCREEN_SORTS[filters.get(
        "sort", "per" if "per_max" in filters else "mcap")]
    df = df.sort_values(sort_col, ascending=asc)

    matched = len(df)
    limit = max(1, min(20, int(filters.get("limit", 10))))
    rows = []
    for code, r in df.iterrows():
        info = uni_search.get(f"KR:{code}") or {}
        market = info.get("market")
        if "market" in filters and market != filters["market"]:
            continue
        rows.append({
            "symbol_id": f"KR:{code}", "name": info.get("name_kr"),
            "market": market, "per": r["per"], "pbr": r["pbr"],
            "eps": r["eps"], "div": r["div"],
            "mcap_eok": r["mcap_eok"], "value_eok": r["value_eok"],
        })
        if len(rows) >= limit:
            break
    return {"date": date, "filters": filters, "matched": matched,
            "returned": len(rows), "results": rows,
            "note": "matched = market 필터 적용 전 건수. 억원 단위: mcap_eok/value_eok"}


_COMMANDS = {
    "search": _cmd_search,
    "screen": _cmd_screen,
    "quote": _cmd_quote,
    "chart": _cmd_chart,
    "fundamental": _cmd_fundamental,
    "verdict": _cmd_verdict,
    "news": _cmd_news,
}


def main(argv: list[str]) -> int:
    try:
        if not argv or argv[0] not in _COMMANDS:
            out = {"error": "usage: chat_tools <command> [arg ...]",
                   "commands": sorted(_COMMANDS)}
        else:
            out = _COMMANDS[argv[0]](*argv[1:])
    except Exception as e:  # noqa: BLE001 — the model reads the error and retries
        out = {"error": f"{type(e).__name__}: {e}"[:400]}
    print(json.dumps(out, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
