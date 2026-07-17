"""/api/analysis — chart / fundamental / news / judge analysis (live).

Agents under src/agents/ populate data/analysis/{kind}/{symbol}/{date}.json;
GET endpoints serve the latest persisted file (verdicts, facts, feed,
summary), POST .../run endpoints trigger an async agent run (analysis_jobs).
The nightly verdict batch (src.agents.verdict_job) pre-bakes chart + judge
for all holdings, so morning GETs are same-day cache hits.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter

from ..core.paths import ANALYSIS_DIR

router = APIRouter(prefix="/api/analysis", tags=["analysis"])

_VALID_KINDS = {"chart", "fundamental", "news", "judge"}


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
    except Exception:
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
    """Batch badge feed for the account list (Level 0 UX): ONE GET returns
    the latest judge action/label/date per symbol plus per-kind latest dates,
    so 25 cards don't fan out 25×4 requests. Consumed by the front-end badge
    work (session C).
    """
    out: dict = {}
    ids = [s.strip().upper() for s in symbols.split(",") if s.strip()][:100]
    for sid in ids:
        kinds = {k: _latest_date(k, sid)
                 for k in ("chart", "fundamental", "news", "judge")}
        entry: dict = {"action_ko": None, "label": None, "status": None,
                       "date": None, "kinds": kinds}
        if kinds["judge"]:
            latest = _latest_file("judge", sid)
            body = (latest or {}).get("body") or {}
            entry.update({
                "action_ko": body.get("action_ko"),
                "label": body.get("label"),
                "status": body.get("status"),
                "confidence": body.get("confidence"),
                "date": body.get("date") or kinds["judge"],
            })
        out[sid] = entry
    return {"symbols": out,
            "queried_at": datetime.utcnow().isoformat() + "Z"}


@router.get("/{kind}/{market}/{code}")
def get_analysis(kind: str, market: str, code: str) -> dict:
    if kind not in _VALID_KINDS:
        return {"error": f"unknown kind '{kind}' (valid: {sorted(_VALID_KINDS)})"}
    symbol_id = f"{market.upper()}:{code}"

    # chart = deterministic fact extraction, computed live (no claude -p here).
    if kind == "chart":
        from ..agents.chart import chart_agent  # lazy: pulls pandas-ta
        try:
            facts = chart_agent.collect_facts(symbol_id, persist=False)
            facts["queried_at"] = datetime.utcnow().isoformat() + "Z"
            return facts
        except Exception as e:  # noqa: BLE001
            return {"kind": "chart", "symbol_id": symbol_id,
                    "error": str(e)[:300],
                    "queried_at": datetime.utcnow().isoformat() + "Z"}

    # fundamental / news: read the latest written file (populated by their agents).
    result = _latest_file(kind, symbol_id)
    return {
        "kind": kind, "symbol_id": symbol_id,
        "exists": result is not None,
        "result": result,
        "queried_at": datetime.utcnow().isoformat() + "Z",
    }


def _published_iso(published) -> Optional[str]:
    """Normalize a feed 'published' string (RFC822 or ISO) to ISO-8601."""
    if not published or not isinstance(published, str):
        return None
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(published).isoformat()
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(published.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


@router.get("/news/feed")
def news_feed(limit: int = 60) -> dict:
    """Landing-page qualitative feed: latest collected signals grouped into
    news (lagging media) / social (coincident, e.g. Reddit/X). arXiv (leading)
    is excluded — it feeds per-symbol evaluate, not the headline feed.
    Refreshed by the 06:00 daily_job collect step.

    Items sharing one URL across themes are merged into a single card carrying
    every theme badge, and the latest daily digest's importance (1~5) is joined
    so the front can star/pin key articles — both NO extra LLM cost.
    """
    from datetime import timezone, timedelta
    from urllib.parse import urlparse
    from ..agents.news.catalog import load_catalog
    from ..core.paths import analysis_news_daily_path, signals_news_path

    def _favicon(u: str):
        """Source favicon (C-tier thumbnail fallback) via Google's favicon
        service — used when an item carries no media image."""
        try:
            host = urlparse(u or "").netloc
        except Exception:  # noqa: BLE001
            host = ""
        return f"https://www.google.com/s2/favicons?domain={host}&sz=64" if host else None

    kst = timezone(timedelta(hours=9))
    now_kst = datetime.now(kst)
    yyyymm = now_kst.strftime("%Y%m")
    cat = load_catalog()
    name_kr = {t.theme_id: t.name_kr for t in cat.themes.values()}

    # importance join: per theme, the latest available daily digest (today,
    # else yesterday — before the 06:00 batch today's file doesn't exist yet).
    dates = [now_kst.strftime("%Y-%m-%d"),
             (now_kst - timedelta(days=1)).strftime("%Y-%m-%d")]
    importance: dict[str, int] = {}
    for t in cat.active_themes():
        for d in dates:
            p = analysis_news_daily_path(t.theme_id, d)
            if not p.exists():
                continue
            try:
                dig = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                break
            for di in dig.get("items", []):
                u = (di.get("url") or "").strip()
                if u:
                    importance[u] = max(importance.get(u, 0),
                                        int(di.get("importance") or 0))
            break  # newest digest per theme only

    news_by_key: dict[str, dict] = {}
    social_by_key: dict[str, dict] = {}
    for t in cat.active_themes():
        p = signals_news_path(t.theme_id, yyyymm)
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            ll = r.get("lead_lag")
            if ll == "coincident":
                bucket = social_by_key
            elif ll in ("lagging", "backbone"):
                bucket = news_by_key
            else:
                continue  # leading (arxiv) skipped for the landing feed
            url = (r.get("url") or "").strip()
            key = url or f"t:{r.get('title')}"
            prev = bucket.get(key)
            if prev is not None:
                # same article surfaced by another theme: merge instead of
                # duplicating the card (shared feeds -> up to 3x repeats)
                if not any(th["id"] == t.theme_id for th in prev["themes"]):
                    prev["themes"].append(
                        {"id": t.theme_id,
                         "kr": name_kr.get(t.theme_id, t.theme_id)})
                if not prev.get("image") and r.get("image"):
                    prev["image"] = r.get("image")
                if not prev.get("published_iso"):
                    prev["published_iso"] = _published_iso(r.get("published"))
                continue
            bucket[key] = {
                "theme": t.theme_id, "theme_kr": name_kr.get(t.theme_id, t.theme_id),
                "themes": [{"id": t.theme_id,
                            "kr": name_kr.get(t.theme_id, t.theme_id)}],
                "title": r.get("title"), "url": url,
                "source": r.get("source_id"), "lead_lag": ll,
                "published": r.get("published"),
                "published_iso": _published_iso(r.get("published")),
                "fetched_at": r.get("fetched_at"),
                "importance": importance.get(url),        # digest join (may be None)
                "image": r.get("image"),                 # A: RSS media thumbnail (may be None)
                "favicon": _favicon(url),                 # C: source favicon fallback
            }

    def _sort_key(x):
        return (_iso_epoch(x.get("published_iso"))
                or _iso_epoch(x.get("fetched_at")) or 0.0)

    news = sorted(news_by_key.values(), key=_sort_key, reverse=True)
    social = sorted(social_by_key.values(), key=_sort_key, reverse=True)
    themes = [{"id": t.theme_id, "name_kr": t.name_kr} for t in cat.active_themes()]
    return {"news": news[:limit], "social": social[:limit],
            "themes": themes, "yyyymm": yyyymm,
            "queried_at": datetime.utcnow().isoformat() + "Z"}


def _iso_epoch(iso) -> Optional[float]:
    """ISO string -> epoch seconds (None-safe). Naive times assumed UTC."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        from datetime import timezone as _tz
        dt = dt.replace(tzinfo=_tz.utc)
    return dt.timestamp()


@router.get("/chart/{market}/{code}/verdict")
def get_chart_verdict(market: str, code: str) -> dict:
    """Latest persisted chart JUDGMENT (claude -p verdict), or exists=False.

    Distinct from GET /chart/{m}/{c} which returns the live deterministic facts.
    This serves the on-demand rulebook verdict written by the chart agent.
    """
    symbol_id = f"{market.upper()}:{code}"
    result = _latest_file("chart", symbol_id)
    return {
        "kind": "chart", "symbol_id": symbol_id,
        "exists": result is not None,
        "result": result,
        "queried_at": datetime.utcnow().isoformat() + "Z",
    }


@router.post("/chart/{market}/{code}/run")
def run_chart_analysis(market: str, code: str) -> dict:
    """Start an on-demand chart (정량) evaluation ASYNCHRONOUSLY (UI 📈 button).

    The verdict takes ~300s (claude -p + rich rulebook) — far longer than the
    nginx/Cloudflare proxy timeouts — so we spawn it in a background thread and
    return immediately. The UI polls GET .../status, then loads GET .../verdict
    once done. Result is persisted to data/analysis/chart/{symbol}/{date}.json.
    """
    symbol_id = f"{market.upper()}:{code}"
    from ..universe import search as uni_search
    item = uni_search.get(symbol_id)
    name = (item or {}).get("name_kr") or symbol_id

    def _fn():
        from ..agents.chart.evaluate import evaluate_symbol  # lazy: spawns claude
        return evaluate_symbol(symbol_id, name=name, timeout_s=480)

    from . import analysis_jobs
    job = analysis_jobs.start("chart", symbol_id, _fn)
    return {"ok": True, "symbol_id": symbol_id, "job": job,
            "queued_at": datetime.utcnow().isoformat() + "Z"}


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
            "queried_at": datetime.utcnow().isoformat() + "Z"}


@router.post("/news/{market}/{code}/run")
def run_news_analysis(market: str, code: str) -> dict:
    """On-demand qualitative evaluation (UI 'analysis' button).

    Reads the pre-built theme states (refreshed by the 06:00 daily_job) and
    runs the per-symbol evaluate (claude -p) once. Result is persisted to
    data/analysis/news/{symbol}/{date}.json (same file GET serves).
    """
    symbol_id = f"{market.upper()}:{code}"
    from ..universe import search as uni_search
    item = uni_search.get(symbol_id)
    name = (item or {}).get("name_kr") or symbol_id

    def _fn():
        from ..agents.news.evaluate import evaluate_symbol  # lazy: spawns claude
        return evaluate_symbol(symbol_id, name=name)

    from . import analysis_jobs
    job = analysis_jobs.start("news", symbol_id, _fn)
    return {"ok": True, "symbol_id": symbol_id, "job": job,
            "queued_at": datetime.utcnow().isoformat() + "Z"}


@router.get("/fundamental/{market}/{code}/facts")
def get_fundamental_facts(market: str, code: str) -> dict:
    """Cheap, read-only fundamental facts (NO LLM). Powers the default 재무 tab
    view: assembled valuation + financial-statement series + derived metrics +
    trend + quality + sector. Shown on tab open before any AI interpretation,
    mirroring how the chart tab shows facts and the news tab shows signals.
    Collection is the daily batch's job; this only reads what is stored.
    """
    symbol_id = f"{market.upper()}:{code}"
    from ..universe import search as uni_search
    item = uni_search.get(symbol_id)
    name = (item or {}).get("name_kr") or symbol_id
    try:
        from ..agents.fundamental.evaluate import assemble_display
        facts = assemble_display(symbol_id, name=name)
        facts["queried_at"] = datetime.utcnow().isoformat() + "Z"
        return facts
    except Exception as e:  # noqa: BLE001
        return {"kind": "fundamental_facts", "symbol_id": symbol_id,
                "error": str(e)[:300],
                "queried_at": datetime.utcnow().isoformat() + "Z"}


@router.get("/judge/{market}/{code}/verdict")
def get_judge_verdict(market: str, code: str) -> dict:
    """Latest persisted 종합 verdict (no LLM call). UI loads this on tab open
    so the previous run is shown immediately."""
    symbol_id = f"{market.upper()}:{code}"
    result = _latest_file("judge", symbol_id)
    return {
        "kind": "judge", "symbol_id": symbol_id,
        "exists": result is not None,
        "result": result,
        "queried_at": datetime.utcnow().isoformat() + "Z",
    }


@router.post("/judge/{market}/{code}/run")
def run_judge_analysis(market: str, code: str) -> dict:
    """On-demand 종합 (🧠) evaluation — single claude -p call applying
    judge_rules.md to chart facts + fundamental facts + news state + any
    cached domain verdicts within 24h. Persisted to
    data/analysis/judge/{symbol}/{date}.json."""
    symbol_id = f"{market.upper()}:{code}"
    from ..universe import search as uni_search
    item = uni_search.get(symbol_id)
    name = (item or {}).get("name_kr") or symbol_id

    def _fn():
        from ..agents.judge.evaluate import evaluate_symbol  # lazy: spawns claude
        return evaluate_symbol(symbol_id, name=name, timeout_s=480)

    from . import analysis_jobs
    job = analysis_jobs.start("judge", symbol_id, _fn)
    return {"ok": True, "symbol_id": symbol_id, "job": job,
            "queued_at": datetime.utcnow().isoformat() + "Z"}


@router.post("/fundamental/{market}/{code}/run")
def run_fundamental_analysis(market: str, code: str) -> dict:
    """On-demand fundamental (재무) evaluation (UI 'analysis' button).

    Runs ONLY the fundamental agent over the symbol's already-collected
    valuation + financials (the 06:00 KST batch — fundamental_job — does the
    collecting). Result is persisted to data/analysis/fundamental/{symbol}/
    {date}.json (same file GET serves).
    """
    symbol_id = f"{market.upper()}:{code}"
    from ..universe import search as uni_search
    item = uni_search.get(symbol_id)
    name = (item or {}).get("name_kr") or symbol_id

    def _fn():
        from ..agents.fundamental.evaluate import evaluate_symbol
        return evaluate_symbol(symbol_id, name=name)

    from . import analysis_jobs
    job = analysis_jobs.start("fundamental", symbol_id, _fn)
    return {"ok": True, "symbol_id": symbol_id, "job": job,
            "queued_at": datetime.utcnow().isoformat() + "Z"}


@router.get("/{kind}/{market}/{code}/status")
def get_job_status(kind: str, market: str, code: str) -> dict:
    """Poll the state of an async analysis job started by POST .../run.
    state: idle | running | done | error | stale. The UI polls this, then loads
    GET .../verdict (chart/judge) or GET .../{m}/{c} (news/fundamental) on done.
    """
    if kind not in _VALID_KINDS:
        return {"error": f"unknown kind '{kind}'"}
    symbol_id = f"{market.upper()}:{code}"
    from . import analysis_jobs
    return {"kind": kind, "symbol_id": symbol_id,
            **analysis_jobs.get_status(kind, symbol_id),
            "queried_at": datetime.utcnow().isoformat() + "Z"}
