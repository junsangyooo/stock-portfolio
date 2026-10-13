"""/api/analysis — comprehensive analysis + per-domain facts (live).

  GET  /analysis/{m}/{c}          the stored comprehensive result
                                  (src/research/analyze) — one call over
                                  chart + fundamental + news
  POST /analysis/{m}/{c}/run      run it asynchronously; poll …/status
  GET  /chart/{m}/{c}             deterministic chart facts, computed live
  GET  /fundamental/{m}/{c}/facts stored fundamentals, assembled
  GET  /news/{m}/{c}/signals      the symbol's themes' recent developments

Only the comprehensive result is a verdict. Per-domain verdicts were removed
on 2026-09-01 — judging a domain alone hardens a one-dimensional conclusion
the later merge cannot undo, so the domains meet inside a single call.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter

from ..core.kst import now as kst_now
from ..universe import search as uni_search
from . import analysis_jobs

router = APIRouter(prefix="/api/analysis", tags=["analysis"])

log = logging.getLogger(__name__)

def _stamp() -> str:
    """Response timestamp — KST, the same clock the agents write dates in."""
    return kst_now().isoformat()


def _display_name(symbol_id: str) -> str:
    item = uni_search.get(symbol_id)
    return (item or {}).get("name_kr") or symbol_id


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
    from ..research.news.read import symbol_news
    return {**symbol_news(f"{market.upper()}:{code}"), "queried_at": _stamp()}


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


@router.get("/chart/{market}/{code}")
def get_chart_facts(market: str, code: str) -> dict:
    """Deterministic fact extraction, computed live (no claude -p here)."""
    symbol_id = f"{market.upper()}:{code}"
    from ..research.chart import chart_agent  # lazy: pulls pandas
    try:
        facts = chart_agent.collect_facts(symbol_id, persist=False)
        facts["queried_at"] = _stamp()
        return facts
    except Exception as e:  # noqa: BLE001 — empty OHLCV must not 500 the tab
        log.warning("chart facts %s failed: %s", symbol_id, e)
        return {"kind": "chart", "symbol_id": symbol_id,
                "error": str(e)[:300], "queried_at": _stamp()}


@router.get("/analysis/{market}/{code}")
def get_analysis(market: str, code: str) -> dict:
    """The stored comprehensive result."""
    symbol_id = f"{market.upper()}:{code}"
    from ..research import analyze
    body = analyze.latest(symbol_id)
    return {"kind": "analysis", "symbol_id": symbol_id, "exists": body is not None,
            "result": {"date": body["date"], "body": body} if body else None,
            "queried_at": _stamp()}


@router.get("/analysis/{market}/{code}/status")
def get_job_status(market: str, code: str) -> dict:
    """Poll the state of an async analysis job started by POST .../run.
    state: idle | running | done | error | stale. The UI polls this, then loads
    GET /analysis/{m}/{c} on done.
    """
    symbol_id = f"{market.upper()}:{code}"
    return {"kind": "analysis", "symbol_id": symbol_id,
            **analysis_jobs.get_status(symbol_id),
            "queried_at": _stamp()}


@router.post("/analysis/{market}/{code}/run")
def run_analysis(market: str, code: str) -> dict:
    """Start the comprehensive analysis ASYNCHRONOUSLY (the 🔄 다시 분석 button).

    It takes minutes — three domains and their dictionaries in one claude call
    — far longer than the nginx/Cloudflare proxy timeouts, so it runs on a
    background thread and this returns immediately. The UI polls
    GET .../status, then loads the result. The chart facts it needs are
    computed inside, once per trading day.
    """
    symbol_id = f"{market.upper()}:{code}"
    name = _display_name(symbol_id)

    def _fn():
        # Imported on the worker thread: it pulls pandas and spawns claude,
        # neither of which belongs on the request path.
        from ..research import analyze
        return analyze.run(symbol_id, name)

    job = analysis_jobs.start(symbol_id, _fn)
    return {"ok": True, "symbol_id": symbol_id, "job": job,
            "queued_at": _stamp()}
