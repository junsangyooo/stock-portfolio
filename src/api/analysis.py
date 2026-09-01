"""/api/analysis — comprehensive analysis + per-domain facts (live).

Two kinds are readable here:
  analysis  the stored comprehensive result (src/research/analyze) — one call
            over chart + fundamental + news, warmed nightly by verdict_job
  chart     deterministic facts, computed live, no LLM

The other two domains have their own dedicated routes because they are facts,
not verdicts: GET /fundamental/{m}/{c}/facts and GET /news/{m}/{c}/signals.
Per-domain verdicts were removed on 2026-09-01 — judging a domain alone
hardens a one-dimensional conclusion the later merge cannot undo, so the
domains now meet inside a single call.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter

from ..core.kst import now as kst_now
from ..core.paths import ANALYSIS_DIR
from ..universe import search as uni_search

router = APIRouter(prefix="/api/analysis", tags=["analysis"])

log = logging.getLogger(__name__)

_VALID_KINDS = ("analysis", "chart")

# Only the two rulebook-heavy agents need the widened ceiling; the others run
# well inside evaluate_symbol's own default.
_RUN_TIMEOUT_S = {"analysis": 1800}  # hang guard only — see verdict_job._TIMEOUT_S


def _stamp() -> str:
    """Response timestamp — KST, the same clock the agents write dates in."""
    return kst_now().isoformat()


def _display_name(symbol_id: str) -> str:
    item = uni_search.get(symbol_id)
    return (item or {}).get("name_kr") or symbol_id


def _bad_kind(kind: str) -> dict:
    return {"error": f"unknown kind '{kind}' (valid: {list(_VALID_KINDS)})"}


def _latest_file(kind: str, symbol_id: str) -> Optional[dict]:
    safe = symbol_id.replace(":", "_")
    d = ANALYSIS_DIR / kind / safe
    if not d.exists():
        return None
    files = sorted(d.glob("*.json"))
    if not files:
        return None
    try:
        return {"path": str(files[-1].name),
                "date": files[-1].stem,
                "body": json.loads(files[-1].read_text())}
    except (json.JSONDecodeError, OSError) as e:
        # A truncated write would otherwise read as "no verdict yet" and blank
        # the symbol's badge with no trace of why.
        log.error("analysis %s/%s unreadable: %s", kind, symbol_id, e)
        return None


def _latest_date(kind: str, symbol_id: str) -> Optional[str]:
    """Newest verdict date for (kind, symbol) WITHOUT parsing the body —
    chart files embed full facts (~100KB) so the summary path must not
    json.loads them. File stems are YYYYMMDD; normalized to YYYY-MM-DD."""
    safe = symbol_id.replace(":", "_")
    d = ANALYSIS_DIR / kind / safe
    if not d.exists():
        return None
    files = sorted(d.glob("*.json"))
    if not files:
        return None
    s = files[-1].stem
    return f"{s[:4]}-{s[4:6]}-{s[6:]}" if len(s) == 8 and s.isdigit() else s


@router.get("/summary")
def analysis_summary(symbols: str) -> dict:
    """Batch badge feed for the account list: ONE GET returns the latest judge
    action/label/date per symbol plus per-kind latest dates, so 25 cards don't
    fan out 25×4 requests."""
    out: dict = {}
    ids = [s.strip().upper() for s in symbols.split(",") if s.strip()][:100]
    for sid in ids:
        kinds = {k: _latest_date(k, sid) for k in _VALID_KINDS}
        entry: dict = {"action_ko": None, "label": None, "status": None,
                       "date": None, "kinds": kinds}
        if kinds["analysis"]:
            body = (_latest_file("analysis", sid) or {}).get("body") or {}
            entry.update({
                "action_ko": body.get("action_ko"),
                "label": body.get("label"),
                "status": body.get("status"),
                "confidence": body.get("confidence"),
                "date": body.get("date") or kinds["analysis"],
            })
        out[sid] = entry
    return {"symbols": out, "queried_at": _stamp()}


@router.get("/news/feed")
def news_feed(limit: int = 60) -> dict:
    """Landing-page qualitative feed (see agents/news/feed.py)."""
    from ..research.news import feed
    return {**feed.build(limit=limit), "queried_at": _stamp()}


@router.get("/news/{market}/{code}/signals")
def news_signals(market: str, code: str) -> dict:
    """Cheap, read-only feed of the qualitative signals connected to a symbol —
    its mapped themes' recent developments + watchlist. NO LLM. Shown by
    default in the news tab so the user sees connected news without running the
    (claude) evaluate, mirroring how the chart tab shows facts on open.
    """
    symbol_id = f"{market.upper()}:{code}"
    from ..research.news.catalog import load_catalog
    from ..research.news.read import theme_state
    from ..market.store import theme_store

    cat = load_catalog()
    rows = [(r["theme_id"], r["relation"], r["weight"])
            for r in theme_store.get(symbol_id)]

    themes_out, devs, seen = [], [], set()
    for theme_id, relation, weight in rows:
        if relation == "macro":
            continue  # macro is background — too generic for the connected list
        st = theme_state(theme_id)
        ct = cat.get(theme_id)
        nm = ct.name_kr if ct else theme_id
        themes_out.append({
            "theme_id": theme_id, "name_kr": nm, "relation": relation,
            "weight": weight, "sentiment": st.get("sentiment", {}),
            "watchlist": st.get("watchlist", []) if relation == "primary" else [],
        })
        for d in st.get("recent_developments", []):
            k = (d.get("url") or "").strip() or (d.get("point") or "")
            if not k or k in seen:
                continue
            seen.add(k)
            devs.append({
                "theme": theme_id, "theme_kr": nm,
                "date": d.get("date"), "point": d.get("point"),
                "lead_lag": d.get("lead_lag"), "weight": d.get("weight"),
                "url": d.get("url"),
            })
    devs.sort(key=lambda x: (x.get("date") or "", x.get("weight") or 0), reverse=True)
    return {"symbol_id": symbol_id, "mapped": bool(rows),
            "themes": themes_out, "developments": devs[:30],
            "queried_at": _stamp()}


@router.get("/fundamental/{market}/{code}/facts")
def get_fundamental_facts(market: str, code: str) -> dict:
    """Cheap, read-only fundamental facts (NO LLM). Powers the default 재무 tab
    view: assembled valuation + financial-statement series + derived metrics +
    trend + quality + sector. Shown on tab open before any AI interpretation,
    mirroring how the chart tab shows facts and the news tab shows signals.
    Collection is the daily batch's job; this only reads what is stored.
    """
    symbol_id = f"{market.upper()}:{code}"
    try:
        from ..research.fundamental.evaluate import assemble_display
        facts = assemble_display(symbol_id, name=_display_name(symbol_id))
        facts["queried_at"] = _stamp()
        return facts
    except Exception as e:  # noqa: BLE001 — a missing store must not 500 the tab
        log.warning("fundamental facts %s failed: %s", symbol_id, e)
        return {"kind": "fundamental_facts", "symbol_id": symbol_id,
                "error": str(e)[:300], "queried_at": _stamp()}


@router.get("/{kind}/{market}/{code}")
def get_analysis(kind: str, market: str, code: str) -> dict:
    if kind not in _VALID_KINDS:
        return _bad_kind(kind)
    symbol_id = f"{market.upper()}:{code}"

    # chart = deterministic fact extraction, computed live (no claude -p here).
    if kind == "chart":
        from ..research.chart import chart_agent  # lazy: pulls pandas-ta
        try:
            facts = chart_agent.collect_facts(symbol_id, persist=False)
            facts["queried_at"] = _stamp()
            return facts
        except Exception as e:  # noqa: BLE001 — empty OHLCV must not 500 the tab
            log.warning("chart facts %s failed: %s", symbol_id, e)
            return {"kind": "chart", "symbol_id": symbol_id,
                    "error": str(e)[:300], "queried_at": _stamp()}

    # analysis: the stored comprehensive result
    result = _latest_file(kind, symbol_id)
    return {"kind": kind, "symbol_id": symbol_id,
            "exists": result is not None, "result": result,
            "queried_at": _stamp()}


@router.get("/{kind}/{market}/{code}/verdict")
def get_verdict(kind: str, market: str, code: str) -> dict:
    """Latest stored analysis (no LLM call). For chart this is distinct from
    GET /chart/{m}/{c}, which returns the live facts."""
    if kind not in _VALID_KINDS:
        return _bad_kind(kind)
    symbol_id = f"{market.upper()}:{code}"
    result = _latest_file(kind, symbol_id)
    return {"kind": kind, "symbol_id": symbol_id,
            "exists": result is not None, "result": result,
            "queried_at": _stamp()}


@router.get("/{kind}/{market}/{code}/status")
def get_job_status(kind: str, market: str, code: str) -> dict:
    """Poll the state of an async analysis job started by POST .../run.
    state: idle | running | done | error | stale. The UI polls this, then loads
    GET .../verdict on done.
    """
    if kind not in _VALID_KINDS:
        return _bad_kind(kind)
    symbol_id = f"{market.upper()}:{code}"
    from . import analysis_jobs
    return {"kind": kind, "symbol_id": symbol_id,
            **analysis_jobs.get_status(kind, symbol_id),
            "queried_at": _stamp()}


@router.post("/{kind}/{market}/{code}/run")
def run_analysis(kind: str, market: str, code: str) -> dict:
    """Start the comprehensive analysis ASYNCHRONOUSLY (the 🔄 다시 분석 button).

    It takes minutes — three domains and their dictionaries in one claude call
    — far longer than the nginx/Cloudflare proxy timeouts, so it runs on a
    background thread and this returns immediately. The UI polls
    GET .../status, then loads the result. The chart facts it needs are
    computed inside, once per trading day.
    """
    if kind != "analysis":
        return {"error": f"'{kind}' is not runnable — only 'analysis' is. "
                         "Per-domain verdicts were removed on 2026-09-01."}
    symbol_id = f"{market.upper()}:{code}"
    name = _display_name(symbol_id)

    def _fn():
        # Imported on the worker thread: it pulls pandas-ta and spawns claude,
        # neither of which belongs on the request path.
        from ..research.analyze.run import analyze
        return analyze(symbol_id, name, timeout_s=_RUN_TIMEOUT_S["analysis"])

    from . import analysis_jobs
    job = analysis_jobs.start(kind, symbol_id, _fn)
    return {"ok": True, "symbol_id": symbol_id, "job": job,
            "queued_at": _stamp()}
