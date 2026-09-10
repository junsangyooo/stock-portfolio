"""Assemble everything the analysis sees — deterministic, no LLM.

The envelope is normalized so the agent can tell which reference governs which
data. Every domain arrives as the same triple:

    {id, reference, data, status}

`reference` is that domain's dictionary (what the fields mean, what the
indicator lies about, which formula we use, what we could not compute).
`data` is this symbol's actual numbers. A domain that failed still appears,
with status="missing" and a reason — a silently absent domain reads as
"nothing to say here", which is not the same thing.

`context` sits outside the domains on purpose: a peer percentile belongs to no
single dictionary, and pretending it does would tell the agent to read it
through, say, the chart reference.

Nothing here knows about an account. The analysis is about the symbol — what
it is worth doing about it depends on what you hold and at what price, and
that is a question for whichever book is asking. Keeping positions out is what
lets one stored result serve both books instead of forking per account.
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


# A gap wider than this between the last stored bar and today is a stopped
# feed, not a holiday. It has to clear a long KR closure (설·추석 plus the
# weekends around it) and the day `asof` loses to UTC on a KR bar, so it is
# deliberately loose — the real freeze this caught ran 12 days and counting.
_BAR_STALE_DAYS = 8


def _bar_age_days(asof: Optional[str], today: datetime) -> Optional[int]:
    try:
        last = datetime.strptime(asof, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None
    return (today.date() - last).days


def _chart(symbol_id: str, force: bool) -> tuple[Optional[dict], str, str]:
    """Chart facts, computing them once if today's row is missing.

    `has_data` says a timeframe had enough bars, never how recent the last
    one is — so a symbol whose feed stopped kept answering "ok" and the
    analysis went out stamped with today's date. Length and recency are
    different questions; both are asked here.
    """
    from ..chart import chart_agent
    facts = chart_agent.collect_facts(symbol_id, force=force)
    tf = facts.get("timeframes") or {}
    if not any(v.get("has_data") for v in tf.values()):
        return None, "missing", "저장된 일봉이 없어 지표를 계산할 수 없다"
    notes = []
    short = [k for k, v in tf.items() if not v.get("has_data")]
    if short:
        notes.append(f"{'·'.join(short)} 타임프레임은 데이터 길이 부족")
    age = _bar_age_days(facts.get("asof"), datetime.now(KST))
    if age is not None and age > _BAR_STALE_DAYS:
        notes.append(f"마지막 일봉이 {facts['asof']}({age}일 전)이다 — "
                     "아래 지표는 전부 그 시점 기준이고 현재가와 무관하다")
    return (facts, "partial" if notes else "ok", " / ".join(notes))


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


def _context(symbol_id: str) -> dict:
    from ...market import peer
    out: dict = {}
    try:
        out["peer"] = peer.percentiles(symbol_id)
    except Exception as e:  # noqa: BLE001 — comparison is enrichment, not core
        out["peer"] = {"error": str(e)[:200]}
    return out


def build(symbol_id: str, name: Optional[str] = None, *,
          force_chart: bool = False) -> dict:
    """The full envelope. Never raises — a broken domain is reported, not fatal.

    Account-free by construction: no book is read here, so the result is the
    same whoever asked."""
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
