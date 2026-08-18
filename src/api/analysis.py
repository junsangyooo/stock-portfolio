"""/api/analysis — chart / fundamental / news / judge analysis (live).

Agents under src/agents/ populate data/analysis/{kind}/{symbol}/{date}.json;
GET endpoints serve the latest persisted file (verdicts, facts, feed,
summary), POST .../run triggers an async agent run (analysis_jobs).
The nightly verdict batch (src.agents.verdict_job) pre-bakes chart + judge
for all holdings, so morning GETs are same-day cache hits.

This module is a thin read/dispatch layer — the analysis itself lives in
src/agents/, and the landing feed in src/agents/news/feed.py.
"""
from __future__ import annotations

import json
import logging
from importlib import import_module
from typing import Optional

from fastapi import APIRouter

from ..core.kst import now as kst_now
from ..core.paths import ANALYSIS_DIR
from ..universe import search as uni_search

router = APIRouter(prefix="/api/analysis", tags=["analysis"])

log = logging.getLogger(__name__)

_VALID_KINDS = ("chart", "fundamental", "news", "judge")

# Only the two rulebook-heavy agents need the widened ceiling; the others run
# well inside evaluate_symbol's own default.
_RUN_TIMEOUT_S = {"chart": 1800, "judge": 1800}  # hang guard only — see verdict_job._TIMEOUT_S


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
        if kinds["judge"]:
            body = (_latest_file("judge", sid) or {}).get("body") or {}
            entry.update({
                "action_ko": body.get("action_ko"),
                "label": body.get("label"),
                "status": body.get("status"),
                "confidence": body.get("confidence"),
                "date": body.get("date") or kinds["judge"],
            })
        out[sid] = entry
    return {"symbols": out, "queried_at": _stamp()}


@router.get("/news/feed")
def news_feed(limit: int = 60) -> dict:
    """Landing-page qualitative feed (see agents/news/feed.py)."""
    from ..agents.news import feed
    return {**feed.build(limit=limit), "queried_at": _stamp()}


@router.get("/news/{market}/{code}/signals")
def news_signals(market: str, code: str) -> dict:
    """Cheap, read-only feed of the qualitative signals connected to a symbol —
    its mapped themes' recent developments + watchlist. NO LLM. Shown by
    default in the news tab so the user sees connected news without running the
    (claude) evaluate, mirroring how the chart tab shows facts on open.
    """
    symbol_id = f"{market.upper()}:{code}"
    from ..agents.news.catalog import load_catalog
    from ..agents.news.state import load_state
    from ..portfolio.store.db import connect

    cat = load_catalog()
    with connect() as conn:
        rows = [tuple(r) for r in conn.execute(
            "SELECT theme_id, relation, weight FROM holding_themes "
            "WHERE symbol_id = ? ORDER BY relation, weight DESC",
            (symbol_id,),
        ).fetchall()]

    themes_out, devs, seen = [], [], set()
    for theme_id, relation, weight in rows:
        if relation == "macro":
            continue  # macro is background — too generic for the connected list
        st = load_state(theme_id)
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
        from ..agents.fundamental.evaluate import assemble_display
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
        from ..agents.chart import chart_agent  # lazy: pulls pandas-ta
        try:
            facts = chart_agent.collect_facts(symbol_id, persist=False)
            facts["queried_at"] = _stamp()
            return facts
        except Exception as e:  # noqa: BLE001 — empty OHLCV must not 500 the tab
            log.warning("chart facts %s failed: %s", symbol_id, e)
            return {"kind": "chart", "symbol_id": symbol_id,
                    "error": str(e)[:300], "queried_at": _stamp()}

    # fundamental / news: read the latest written file (populated by their agents).
    result = _latest_file(kind, symbol_id)
    return {"kind": kind, "symbol_id": symbol_id,
            "exists": result is not None, "result": result,
            "queried_at": _stamp()}


@router.get("/{kind}/{market}/{code}/verdict")
def get_verdict(kind: str, market: str, code: str) -> dict:
    """Latest persisted rulebook verdict for any kind (no LLM call). For chart
    this is distinct from GET /chart/{m}/{c}, which returns the live facts."""
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
    GET .../verdict (chart/judge) or GET .../{m}/{c} (news/fundamental) on done.
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
    """Start an on-demand agent run ASYNCHRONOUSLY (the UI's analysis buttons).

    A verdict takes ~300s (claude -p + a rich rulebook) — far longer than the
    nginx/Cloudflare proxy timeouts — so it runs on a background thread and
    this returns immediately. The UI polls GET .../status, then loads the
    result once done. Every agent persists to data/analysis/{kind}/{symbol}/.
    """
    if kind not in _VALID_KINDS:
        return _bad_kind(kind)
    symbol_id = f"{market.upper()}:{code}"
    name = _display_name(symbol_id)
    timeout_s = _RUN_TIMEOUT_S.get(kind)

    def _fn():
        # Imported here, on the worker thread: evaluate pulls pandas-ta and
        # spawns claude, neither of which belongs on the request path.
        evaluate = import_module(f"..agents.{kind}.evaluate", __package__)
        kwargs = {"name": name}
        if timeout_s:
            kwargs["timeout_s"] = timeout_s
        return evaluate.evaluate_symbol(symbol_id, **kwargs)

    from . import analysis_jobs
    job = analysis_jobs.start(kind, symbol_id, _fn)
    return {"ok": True, "symbol_id": symbol_id, "job": job,
            "queued_at": _stamp()}
