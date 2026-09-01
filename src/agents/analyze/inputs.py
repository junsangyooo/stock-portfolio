"""Assemble everything the analysis sees — deterministic, no LLM.

The envelope is normalized so the agent can tell which reference governs which
data. Every domain arrives as the same triple:

    {id, reference, data, status}

`reference` is that domain's dictionary (what the fields mean, what the
indicator lies about, which formula we use, what we could not compute).
`data` is this symbol's actual numbers. A domain that failed still appears,
with status="missing" and a reason — a silently absent domain reads as
"nothing to say here", which is not the same thing.

`context` sits outside the domains on purpose: holdings and peer percentiles
belong to no single dictionary, and pretending they do would tell the agent to
interpret a percentile through, say, the chart reference.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from ...core.kst import KST
from ...core.paths import split_symbol

log = logging.getLogger(__name__)

_REFS = {
    "chart": Path(__file__).resolve().parents[1] / "chart" / "chart_rules.md",
    "fundamental": Path(__file__).resolve().parents[1] / "fundamental" / "fundamental_rules.md",
    "news": Path(__file__).resolve().parents[1] / "news" / "news_rules.md",
}

_TITLE = {"chart": "차트 (정량)", "fundamental": "재무 (펀더멘털)",
          "news": "뉴스 (정성)"}


def _reference(domain: str) -> str:
    return _REFS[domain].read_text(encoding="utf-8")


def _chart(symbol_id: str, force: bool) -> tuple[Optional[dict], str, str]:
    """Chart facts, computing them once if today's row is missing."""
    from ..chart import chart_agent
    facts = chart_agent.collect_facts(symbol_id, force=force)
    tf = facts.get("timeframes") or {}
    if not any(v.get("has_data") for v in tf.values()):
        return None, "missing", "저장된 일봉이 없어 지표를 계산할 수 없다"
    stale = [k for k, v in tf.items() if not v.get("has_data")]
    return (facts, "partial" if stale else "ok",
            f"{'·'.join(stale)} 타임프레임은 데이터 길이 부족" if stale else "")


def _fundamental(symbol_id: str, name: str) -> tuple[Optional[dict], str, str]:
    from ..fundamental.evaluate import assemble_display
    data = assemble_display(symbol_id, name=name)
    avail = data.get("data_available") or {}
    if data.get("is_etf") or avail.get("is_etf"):
        return data, "ok", "ETF — 재무제표 대신 NAV·보유구성 기준"
    if not data.get("n_periods"):
        return None, "missing", "재무제표가 수집되지 않았다"
    if not (data.get("valuation") or {}):
        return data, "partial", "밸류에이션(PER·PBR)이 없어 재무제표만"
    return data, "ok", ""


def _news(symbol_id: str) -> tuple[Optional[dict], str, str]:
    """Theme states plus the long-memory summaries the 7-day window misses."""
    from ..news.evaluate import assemble_inputs as themes_for
    from ..news.read import theme_state
    data = themes_for(symbol_id)
    mapped = (data.get("data_available") or {}).get("themes_mapped")
    if not mapped:
        return None, "missing", "이 종목에 매핑된 분야가 없다"
    # theme_state already carries week/month summaries; surface them at the top
    # so the agent does not have to dig for the long memory.
    long_memory = []
    for block in data.get("themes", []):
        st = theme_state(block["theme_id"])
        if st.get("week_summary") or st.get("month_summary"):
            long_memory.append({
                "theme_id": block["theme_id"],
                "relation": block.get("relation"),
                "week": st.get("week_summary"),
                "month": st.get("month_summary"),
            })
    data["long_memory"] = long_memory
    if not data.get("has_content", True) and not long_memory:
        return data, "partial", "매핑은 있으나 최근 수집된 내용이 적다"
    return data, "ok", ""


def _holding(symbol_id: str) -> Optional[dict]:
    """Total exposure across every human account — the analysis cares about
    the position, not which account it sits in. Quant positions are a separate
    book and are not folded in here."""
    from ...market.store import quote_store
    from ...portfolio.store.db import connect
    with connect() as conn:
        rows = conn.execute(
            "SELECT avg_cost, qty FROM holdings WHERE symbol_id = ?",
            (symbol_id,)).fetchall()
    qty = sum(float(r["qty"] or 0) for r in rows)
    if qty <= 0:
        return None
    cost = sum(float(r["avg_cost"] or 0) * float(r["qty"] or 0) for r in rows)
    avg = cost / qty
    q = quote_store.get(symbol_id)
    px = float(getattr(q, "price", 0) or 0) if q else 0.0
    return {
        "qty": qty,
        "avg_cost": round(avg, 2),
        "current_price": px or None,
        "eval": round(px * qty) if px else None,
        "pnl_pct": round((px - avg) / avg * 100, 2) if (px and avg) else None,
    }


def _context(symbol_id: str) -> dict:
    from ...market import peer
    out: dict = {}
    try:
        out["peer"] = peer.percentiles(symbol_id)
    except Exception as e:  # noqa: BLE001 — comparison is enrichment, not core
        out["peer"] = {"error": str(e)[:200]}
    try:
        out["holding"] = _holding(symbol_id)
    except Exception as e:  # noqa: BLE001
        out["holding"] = {"error": str(e)[:200]}
    return out


def build(symbol_id: str, name: Optional[str] = None, *,
          force_chart: bool = False) -> dict:
    """The full envelope. Never raises — a broken domain is reported, not fatal."""
    name = name or symbol_id
    market, _ = split_symbol(symbol_id)
    domains = []
    for did in ("chart", "fundamental", "news"):
        try:
            if did == "chart":
                data, status, note = _chart(symbol_id, force_chart)
            elif did == "fundamental":
                data, status, note = _fundamental(symbol_id, name)
            else:
                data, status, note = _news(symbol_id)
        except Exception as e:  # noqa: BLE001 — one domain is not the analysis
            log.warning("analyze input %s/%s failed: %s", symbol_id, did,
                        str(e)[:200])
            data, status, note = None, "missing", f"수집 실패: {str(e)[:150]}"
        domains.append({"id": did, "title_ko": _TITLE[did],
                        "reference": _reference(did), "data": data,
                        "status": status, "note_ko": note})

    return {
        "symbol": {"symbol_id": symbol_id, "name": name, "market": market},
        "asof": datetime.now(KST).strftime("%Y-%m-%d"),
        "domains": domains,
        "context": _context(symbol_id),
        "available": [d["id"] for d in domains if d["status"] != "missing"],
    }
