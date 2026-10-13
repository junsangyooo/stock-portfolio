"""Lookup CLI for the account chat agent.

The chat backend (`src/api/chat.py`) runs `claude -p` with exactly one
allowed command family, by absolute path from an empty working directory:

    <repo>/.venv/bin/python -m src.portfolio.chat.tools <command> [arg ...]

Account commands read one account only: the one the server bound this
process to (PDASH_CHAT_ACCOUNT, signed in PDASH_CHAT_SIG — see
core.security.chat_binding). No command takes an account id, so the agent has
no argument with which to name another. Without a valid binding they refuse.

Market, analysis and news commands know no account. They read what the
shared layers expose; `chart` and `fundamental` also write market.db on a
symbol's first sight (`_ensure_tracked` pulls its OHLCV, `fundamental` its
statements) so an untracked candidate can be examined at all — shared facts
about a symbol, never the account book. One JSON object goes to stdout;
failures come back as {"error": ...} with exit 0 so the model can read and
recover from them.

Commands:
  account                     this account: row, holdings, cash
  trades [k=v ...]            symbol= since= until= (KST days) limit= offset=
  snapshots [k=v ...]         currency=KRW days=90
  chats                       this account's chat threads
  chat <session_id>           one of them, messages included
  search <query>              universe fuzzy search (KR+US, top 8)
  quote <SYMBOL_ID>           latest price/prev-close (KR:005930 | US:AAPL)
  chart <SYMBOL_ID>           live chart facts (indicators, no LLM)
  fundamental <SYMBOL_ID>     valuation + financials facts (trimmed)
  verdict <SYMBOL_ID> [date]  stored comprehensive analysis (latest, or that day)
  news <SYMBOL_ID>            its themes' news (macro included) + title mentions
  news_search [words] [k=v]   q words; theme=<id>|none since= until= source=
                              min_importance= limit= offset=
  article <signal_id>         every stored field of one article
  theme <theme_id>            a theme now (7-day items, sentiment, watchlist,
                              weekly/monthly summary)
  theme_history <theme_id> [k=v]  kind=daily|weekly|monthly since= until=
                              limit= offset=
  catalog                     themes with definitions, sources with status
  screen k=v ...              KR whole-market factor screen (pykrx daily);
                              market=NASD|NYSE|AMEX면 KIS 해외조건검색으로 전환
                              (US 키: per/eps/price/rate/volume/mcap/value
                               _min|_max — mcap·value는 백만달러)
                              keys: per_min per_max pbr_min pbr_max div_min
                                    eps_min mcap_min mcap_max value_min
                                    market=KOSPI|KOSDAQ sort limit
                              (억원 단위: mcap_*, value_min. div=배당수익률 %)
  theme_symbols <theme_id>    theme → symbols (tagged + news-mentioned)
                              opt: days=7 limit=30

stdout carries the JSON and nothing else: pykrx prints a KRX login warning
straight to stdout, so command output is captured and only the result object
is written to the real stream.
"""
from __future__ import annotations

import contextlib
import hmac
import io
import json
import os
import re
import sys


# A symbol id becomes a path (stored analyses) and a date a file name, so
# both are held to their real shape: `verdict KR:005930 ../../..` must not
# walk out of the analysis tree.
_SYMBOL = re.compile(r"(KR|US):[A-Z0-9][A-Z0-9. \-]{0,11}")
_DAY = re.compile(r"[0-9]{4}-?[0-9]{2}-?[0-9]{2}")


def _sid(symbol_id: str) -> str:
    """The argument as a canonical symbol id, or ValueError."""
    sid = symbol_id.strip().upper()
    if not _SYMBOL.fullmatch(sid):
        raise ValueError(f"symbol_id must be KR:code or US:ticker, got {sid!r}")
    return sid


def _kv(args, keys: set) -> tuple[dict, list[str]]:
    """key=value options (only `keys`) and the bare words left over."""
    opts, words = {}, []
    for a in args:
        k, sep, v = a.partition("=")
        if not sep:
            words.append(a)
            continue
        if k not in keys:
            raise ValueError(f"options are {sorted(keys)}, got {k!r}")
        opts[k] = v
    return opts, words


# ── this account (bound by the server, never by an argument) ──────────

def _account() -> str:
    aid = os.environ.get("PDASH_CHAT_ACCOUNT") or ""
    sig = os.environ.get("PDASH_CHAT_SIG") or ""
    from .. import security
    if not aid or not sig or not hmac.compare_digest(sig, security.chat_binding(aid)):
        raise PermissionError("계좌 명령은 대화가 열린 계좌에서만 쓸 수 있다 (계좌 연결 없음)")
    return aid


def _cmd_account() -> dict:
    from ..store import account_store, balance_store, holding_store
    aid = _account()
    return {"account": account_store.get(aid),          # no pin_hash
            "holdings": holding_store.list_by_account(aid),
            "cash": balance_store.get_all(aid)}


def _cmd_trades(*args: str) -> dict:
    from ..store import trade_store
    aid = _account()
    o, words = _kv(args, {"symbol", "since", "until", "limit", "offset"})
    if words:
        raise ValueError(f"trades takes key=value options only, got {words!r}")
    limit = max(1, min(200, int(o.get("limit", 50))))
    offset = max(0, int(o.get("offset", 0)))
    rows = trade_store.list_by_account(
        aid, limit + offset + 1, symbol_id=(o.get("symbol") or "").upper() or None,
        since=o.get("since"), until=o.get("until"))
    page = rows[offset:offset + limit]
    return {"offset": offset, "returned": len(page),
            "next_offset": offset + limit if len(rows) > offset + limit else None,
            "note": "최신순. traded_at = unix 초, KST 일자 필터",
            "trades": page}


def _cmd_snapshots(*args: str) -> dict:
    from ..store import snapshot_store
    aid = _account()
    o, words = _kv(args, {"currency", "days"})
    if words:
        raise ValueError(f"snapshots takes key=value options only, got {words!r}")
    ccy = (o.get("currency") or "KRW").upper()
    days = max(1, min(3650, int(o.get("days", 90))))
    rows = snapshot_store.read(aid, ccy, days)
    return {"currency": ccy, "days": days, "n": len(rows),
            "note": "날짜 오름차순, 하루 한 줄 (eval·cost·pnl·cash·total_assets)",
            "snapshots": rows}


def _cmd_chats() -> dict:
    from ..store import chat_store
    return {"chats": chat_store.list_for(_account())}


def _cmd_chat(session_id: str) -> dict:
    from ..store import chat_store
    t = chat_store.get(_account(), session_id.strip())
    return t if t else {"error": "이 계좌에 그런 대화가 없다"}


# ── market and analysis (no account) ──────────────────────────────────

def _cmd_search(query: str) -> dict:
    from ...universe import search as uni_search
    rows = uni_search.search(query, limit=8)
    return {"query": query, "results": [
        {"symbol_id": r.get("symbol_id"), "name_kr": r.get("name_kr"),
         "name_en": r.get("name_en"), "market": r.get("market"),
         "is_etf": r.get("is_etf")} for r in rows]}


def _cmd_quote(symbol_id: str) -> dict:
    from ...market import quotes
    return quotes.quotes([_sid(symbol_id)])


def _ensure_tracked(symbol_id: str) -> None:
    """First sight of a symbol: pull ~3y OHLCV on the spot (≈2s).

    chart/fundamental only read stored bars, so an untracked symbol used
    to come back no_data — the discovery agent found candidates in the news
    and then could not examine a single one of them (cycle #1, 2026-08-26).
    """
    from ...market.store import ohlcv_store
    if ohlcv_store.last_ts(symbol_id) == 0:
        from ...market.pipeline.gap_filler import fill_one
        fill_one(symbol_id)


def _cmd_chart(symbol_id: str) -> dict:
    from ...research.chart import facts
    sid = _sid(symbol_id)
    _ensure_tracked(sid)
    return facts.collect_facts(sid, persist=False)


def _cmd_fundamental(symbol_id: str) -> dict:
    from ...market.store import financials_store
    from ...research.fundamental.assemble import assemble_display
    from ...universe import search as uni_search
    sid = _sid(symbol_id)
    _ensure_tracked(sid)                       # valuation derives from closes
    if financials_store.read(sid).empty:       # first sight: statements too (~5s)
        from ...market.pipeline.fundamental_backfill import fill_one
        fill_one(sid)
    facts = assemble_display(
        sid, name=(uni_search.get(sid) or {}).get("name_kr") or sid)
    series = facts.get("financials_series")
    if isinstance(series, list) and len(series) > 4:
        facts["financials_series"] = series[-4:]
        facts["financials_series_note"] = f"최근 4분기만 표시 (전체 {len(series)}분기)"
    return facts


def _cmd_verdict(symbol_id: str, date: str = "") -> dict:
    from ...research import analyze
    sid = _sid(symbol_id)
    if date and not _DAY.fullmatch(date):
        raise ValueError(f"date must be YYYY-MM-DD, got {date!r}")
    body = analyze.on(sid, date) if date else analyze.latest(sid)
    return {"symbol_id": sid, "exists": body is not None, "result": body}


# ── news (every collected article; no account) ────────────────────────

def _cmd_news(symbol_id: str) -> dict:
    from ...research.news import read
    return read.symbol_news(_sid(symbol_id), include_macro=True, mentions=True)


def _cmd_news_search(*args: str) -> dict:
    from ...research.news import read
    o, words = _kv(args, {"q", "theme", "since", "until", "source",
                          "min_importance", "limit", "offset"})
    q = " ".join(filter(None, [o.get("q"), *words])) or None
    mi = o.get("min_importance")
    return read.search(q=q, theme=o.get("theme"), since=o.get("since"),
                       until=o.get("until"), source=o.get("source"),
                       min_importance=int(mi) if mi else None,
                       limit=int(o.get("limit", 20)), offset=int(o.get("offset", 0)))


def _cmd_article(signal_id: str) -> dict:
    from ...research.news import read
    a = read.article(int(signal_id))
    return a if a else {"error": f"no article {signal_id}"}


def _cmd_theme(theme_id: str) -> dict:
    from ...research.news import read
    return read.theme_state(theme_id.strip())


def _cmd_theme_history(theme_id: str, *args: str) -> dict:
    from ...research.news import read
    o, words = _kv(args, {"kind", "since", "until", "limit", "offset"})
    if words:
        raise ValueError(f"theme_history takes key=value options only, got {words!r}")
    return read.theme_history(theme_id.strip(), kind=o.get("kind", "daily"),
                              since=o.get("since"), until=o.get("until"),
                              limit=int(o.get("limit", 30)),
                              offset=int(o.get("offset", 0)))


def _cmd_catalog() -> dict:
    from ...research.news import read
    return read.catalog()


_SCREEN_NUM_KEYS = {"per_min", "per_max", "pbr_min", "pbr_max", "div_min",
                    "eps_min", "mcap_min", "mcap_max", "value_min", "limit"}
_SCREEN_COLS = {"per": "per", "pbr": "pbr", "div": "div", "eps": "eps",
                "mcap": "mcap_eok", "value": "value_eok"}   # key stem -> column
_SCREEN_SORTS = {  # key -> (column, ascending)
    "per": ("per", True), "pbr": ("pbr", True), "div": ("div", False),
    "mcap": ("mcap_eok", False), "value": ("value_eok", False),
}

_US_EXCHANGES = {"NASD", "NASDAQ", "NAS", "NYSE", "NYS", "AMEX", "AMS"}
_US_SCREEN_KEYS = {"per": "per", "eps": "eps", "price": "price",
                   "rate": "rate", "volume": "volume",
                   "mcap": "mcap_musd", "value": "value_musd"}


def _screen_us(market: str, args) -> dict:
    """KIS 해외주식조건검색 (HHDFS76410000). 키: per/eps/price/rate/volume
    _min|_max, mcap·value(_min|_max, 백만달러), limit. PBR·배당 필터 없음."""
    filters, limit = {}, 30
    for a in args:
        k, sep, v = a.partition("=")
        if not sep:
            raise ValueError(f"screen args must be key=value, got {a!r}")
        if k == "market":
            continue
        if k == "limit":
            limit = max(1, min(50, int(float(v))))
            continue
        stem, _, bound = k.rpartition("_")
        if bound not in ("min", "max") or stem not in _US_SCREEN_KEYS:
            raise ValueError(
                f"US screen keys are {sorted(_US_SCREEN_KEYS)}(_min|_max), "
                f"market, limit — got {k!r}")
        filters[f"{_US_SCREEN_KEYS[stem]}_{bound}"] = float(v)
    from ...market.source import us_screener
    return us_screener.screen(market, filters, limit=limit)


def _cmd_screen(*args: str) -> dict:
    for a in args:
        k, sep, v = a.partition("=")
        if sep and k == "market" and v.upper() in _US_EXCHANGES:
            return _screen_us(v.upper(), args)
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

    from ...market.source import krx_screener
    from ...universe import search as uni_search
    date, df = krx_screener.load()

    for stem, col in _SCREEN_COLS.items():
        if f"{stem}_min" in filters:
            df = df[df[col] >= filters[f"{stem}_min"]]
        if f"{stem}_max" in filters:
            df = df[df[col] <= filters[f"{stem}_max"]]
            if stem in ("per", "pbr"):
                # PER/PBR upper bounds imply >0 — pykrx reports 0 for 적자/미산출.
                df = df[df[col] > 0]

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


def _cmd_theme_symbols(theme_id: str, *args: str) -> dict:
    from ...research.news.reverse import symbols_for_theme
    opts = {"days": 7, "limit": 30}
    for a in args:
        k, sep, v = a.partition("=")
        if not sep or k not in opts:
            raise ValueError(f"theme_symbols opts are days=/limit=, got {a!r}")
        opts[k] = int(v)
    return symbols_for_theme(theme_id.strip(), **opts)


_COMMANDS = {
    "account": _cmd_account,
    "trades": _cmd_trades,
    "snapshots": _cmd_snapshots,
    "chats": _cmd_chats,
    "chat": _cmd_chat,
    "search": _cmd_search,
    "screen": _cmd_screen,
    "quote": _cmd_quote,
    "chart": _cmd_chart,
    "fundamental": _cmd_fundamental,
    "verdict": _cmd_verdict,
    "news": _cmd_news,
    "news_search": _cmd_news_search,
    "article": _cmd_article,
    "theme": _cmd_theme,
    "theme_history": _cmd_theme_history,
    "catalog": _cmd_catalog,
    "theme_symbols": _cmd_theme_symbols,
}


# A tool answer is not paid for once. It joins the agent's transcript and is
# re-sent on every later turn, so one oversized reply is billed again on each
# of them. The ceiling sits above what the real tools return — a stored
# analysis is 14~19KB, 200 candles ~12KB, the chart fact set is the largest
# and still under it — so nothing an agent actually reads gets cut. What it
# stops is the answer nobody sized — a whole market's list in one reply.
_MAX_OUT_CHARS = 40_000


def dump_capped(out) -> str:
    """Serialize one tool answer, trimmed to fit.

    Lists are halved first, so the answer keeps its shape: the model still
    sees the keys, the first rows, and `_truncated` saying what was cut —
    the cue to narrow the query. When there is nothing structural left to
    trim the answer degrades to a head plus that same marker, never to a
    sliced string: handing the model invalid JSON is worse than a silent cut,
    and a silent cut already reads as "that is all there is".
    """
    s = json.dumps(out, ensure_ascii=False, default=str)
    if len(s) <= _MAX_OUT_CHARS:
        return s
    if isinstance(out, dict):
        sizes = {k: len(v) for k, v in out.items() if isinstance(v, list)}
        trimmed, cut = dict(out), {}
        while len(s) > _MAX_OUT_CHARS and any(len(trimmed[k]) > 1 for k in sizes):
            key = max((k for k in sizes if len(trimmed[k]) > 1),
                      key=lambda k: len(trimmed[k]))
            trimmed[key] = trimmed[key][:max(1, len(trimmed[key]) // 2)]
            cut[key] = f"{len(trimmed[key])}/{sizes[key]}"
            s = json.dumps({**trimmed, "_truncated": cut},
                           ensure_ascii=False, default=str)
        if len(s) <= _MAX_OUT_CHARS:
            return s
    head = s[:_MAX_OUT_CHARS // 2]          # escaping can at most double it
    return json.dumps({"_truncated": f"{len(head):,}/{len(s):,}자 — 질의를 좁혀라",
                       "head": head}, ensure_ascii=False)


def main(argv: list[str]) -> int:
    # Dependencies print to stdout (pykrx: "KRX 로그인 실패: ..."), which would
    # sit in front of the JSON the model has to read. Swallow anything the
    # command writes and emit only the result object.
    noise = io.StringIO()
    try:
        with contextlib.redirect_stdout(noise):
            if not argv or argv[0] not in _COMMANDS:
                out = {"error": "usage: tools <command> [arg ...]",
                       "commands": sorted(_COMMANDS)}
            else:
                out = _COMMANDS[argv[0]](*argv[1:])
    except Exception as e:  # noqa: BLE001 — the model reads the error and retries
        out = {"error": f"{type(e).__name__}: {e}"[:400]}
    print(dump_capped(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
