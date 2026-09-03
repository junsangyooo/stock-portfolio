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

import logging

from fastapi import APIRouter

from ..core.kst import now as kst_now
from ..universe import search as uni_search
from . import analysis_jobs

router = APIRouter(prefix="/api/analysis", tags=["analysis"])

log = logging.getLogger(__name__)

_VALID_KINDS = ("analysis", "chart")

# Hang guard for the one runnable kind — the job runner's ceiling, which is
# also what /status uses to call a job stale. verdict_job has its own copy.
_RUN_TIMEOUT_S = {"analysis": analysis_jobs.RUN_TIMEOUT_S}


def _stamp() -> str:
    """Response timestamp — KST, the same clock the agents write dates in."""
    return kst_now().isoformat()


def _display_name(symbol_id: str) -> str:
    item = uni_search.get(symbol_id)
    return (item or {}).get("name_kr") or symbol_id


def _bad_kind(kind: str) -> dict:
    return {"error": f"unknown kind '{kind}' (valid: {list(_VALID_KINDS)})"}


@router.get("/summary")
def analysis_summary(symbols: str) -> dict:
    """Batch badge feed for the account list: ONE GET returns the latest label
    and date per symbol, so 25 cards don't fan out 25 requests."""
    from ..research import analyze
    ids = [s.strip().upper() for s in symbols.split(",") if s.strip()][:100]
    return {"symbols": analyze.summary(ids), "queried_at": _stamp()}


@router.get("/news/feed")
def news_feed(limit: int = 60) -> dict:
    """Landing-page qualitative feed (see research/news/feed.py)."""
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
        from ..research.chart import chart_agent  # lazy: pulls pandas
        try:
            facts = chart_agent.collect_facts(symbol_id, persist=False)
            facts["queried_at"] = _stamp()
            return facts
        except Exception as e:  # noqa: BLE001 — empty OHLCV must not 500 the tab
            log.warning("chart facts %s failed: %s", symbol_id, e)
            return {"kind": "chart", "symbol_id": symbol_id,
                    "error": str(e)[:300], "queried_at": _stamp()}

    # analysis: the stored comprehensive result
    from ..research import analyze
    body = analyze.latest(symbol_id)
    return {"kind": kind, "symbol_id": symbol_id, "exists": body is not None,
            "result": {"date": body["date"], "body": body} if body else None,
            "queried_at": _stamp()}


@router.get("/{kind}/{market}/{code}/status")
def get_job_status(kind: str, market: str, code: str) -> dict:
    """Poll the state of an async analysis job started by POST .../run.
    state: idle | running | done | error | stale. The UI polls this, then loads
    GET /analysis/{m}/{c} on done.
    """
    if kind not in _VALID_KINDS:
        return _bad_kind(kind)
    symbol_id = f"{market.upper()}:{code}"
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
        # Imported on the worker thread: it pulls pandas and spawns claude,
        # neither of which belongs on the request path.
        from ..research import analyze
        return analyze.run(symbol_id, name,
                           timeout_s=_RUN_TIMEOUT_S["analysis"])

    job = analysis_jobs.start(kind, symbol_id, _fn)
    return {"ok": True, "symbol_id": symbol_id, "job": job,
            "queued_at": _stamp()}
