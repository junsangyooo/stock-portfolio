"""L4 step 3 — per-symbol qualitative (news/trend/issue) evaluation.

Triggered on-demand by the UI 📰 button (POST /api/analysis/news/{m}/{c}/run).
Reads the symbol's theme mapping (holding_themes) + each theme's rolling state
(refreshed by the 06:00 daily_job: collect→digest→state) and applies
src/agents/news/news_rules.md via claude -p to produce a qualitative verdict.

The rulebook drives a 4-lens read (per news_rules.md):
  §B priced-in → §C direction/magnitude → §D timing → §E second-order,
then §F attributes the theme signal to THIS symbol (primary / related+weight /
macro background).

Output uses the SAME 5-level label vocabulary as the chart / fundamental agents
so the 🧠 Judge can fuse all three. Written to data/analysis/news/{symbol}/
{date}.json, served by GET /api/analysis/news/{market}/{code}.

⚠️ No price/volume input by design — "priced-in" is inferred from the
information-diffusion stage (lead_lag / novelty / watchlist), NOT observed price
(rulebook §I). Cross-domain fusion is the Judge's job.

Pipeline:
  assemble_inputs — holding_themes + per-theme state (primary full / related
                    brief + edge_relation from the primary theme's themes.json
                    edges / macro brief). Read-only — does NOT collect.
  _judge          — applies news_rules.md via claude -p → rich verdict
                    (priced_in / time_horizon / second_order / drivers / risks /
                    event_tags / evidence / watch + layperson summary_ko).
  evaluate_symbol — assemble → judge → persist.
"""
from __future__ import annotations

import json
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from typing import Optional

from ...core.kst import KST as _KST
from ...portfolio.store.db import connect
from ..llm_backend import ClaudeCliBackend, SLOW_MODEL, _parse_json
from ..verdict import clamp_confidence, clamp_score, label_from_score, persist
from .catalog import Catalog, load_catalog
from .state import load_state

_RECENT_PRIMARY = 8   # recent developments injected for the primary theme
_RECENT_RELATED = 3   # ... for each related/macro theme (kept brief)

# 5-level label vocabulary, identical to the chart / fundamental agents.
_PRICED_IN = ("unpriced", "partially_priced", "mostly_priced")
_HORIZONS = ("immediate", "near", "mid", "long")

_RULES = (Path(__file__).parent / "news_rules.md").read_text(encoding="utf-8")


def get_symbol_themes(symbol_id: str, conn) -> list[dict]:
    """Theme mapping for one symbol from holding_themes (primary first)."""
    rows = conn.execute(
        "SELECT theme_id, relation, weight FROM holding_themes "
        "WHERE symbol_id = ? ORDER BY relation, weight DESC",
        (symbol_id,),
    ).fetchall()
    return [{"theme_id": r[0], "relation": r[1], "weight": r[2]} for r in rows]


# ── input assembly ────────────────────────────────────────────────────────

def _trim_dev(d: dict) -> dict:
    """Keep only the fields the rulebook references (novelty may be absent on
    pre-fix state files — the rulebook §B falls back to lead_lag/watchlist)."""
    out = {"date": d.get("date"), "point": d.get("point"),
           "lead_lag": d.get("lead_lag"), "weight": d.get("weight")}
    if "novelty" in d:
        out["novelty"] = d.get("novelty")
    return out


def _theme_block(state: dict, relation: str, weight: float,
                 edge_relation: Optional[str], name_kr: str, full: bool) -> dict:
    """One theme's state shaped for the prompt. primary=full, others=brief.

    `relation`      = holding_themes relation (primary | related | macro) — §F.
    `edge_relation` = themes.json edge from the PRIMARY theme (upstream/
                      downstream/...) — §E read-through path (related only).
    """
    recent = state.get("recent_developments", []) or []
    n = _RECENT_PRIMARY if full else _RECENT_RELATED
    blk = {
        "theme_id": state.get("theme_id"),
        "name_kr": name_kr,
        "relation": relation,
        "weight": round(float(weight), 2),
        "horizon_summary": state.get("horizon_summary") or "",
        "sentiment": state.get("sentiment", {}) or {},
        "recent_developments": [_trim_dev(d) for d in recent[:n]],
    }
    if edge_relation:
        blk["edge_relation"] = edge_relation
    if full:
        blk["watchlist"] = (state.get("watchlist") or [])[:6]
        blk["last_shift"] = state.get("last_shift", "")
    return blk


def assemble_inputs(symbol_id: str, *, catalog: Optional[Catalog] = None,
                    conn=None) -> dict:
    """Gather the symbol's theme states for evaluation (read-only).

    primary theme  → full state (horizon + sentiment + recent + watchlist).
    related themes → brief state + `edge_relation` (its position in the primary
                     theme's themes.json value chain → §E read-through path).
    macro          → brief state, always injected as the backbone (§F3).
    """
    catalog = catalog or load_catalog()
    with (connect() if conn is None else nullcontext(conn)) as c:
        themes = get_symbol_themes(symbol_id, c)

    # edge_relation map: the primary theme's themes.json related edges
    # ({related_theme_id: relation}) — used to annotate related themes (§E1).
    primary_id = next((t["theme_id"] for t in themes
                       if t["relation"] == "primary"), None)
    edge_map: dict[str, str] = {}
    if primary_id:
        pt = catalog.get(primary_id)
        if pt:
            for e in pt.related:
                if e.get("theme") and e.get("relation"):
                    edge_map[e["theme"]] = e["relation"]

    blocks: list[dict] = []
    used: list[dict] = []
    have_macro = False
    for t in themes:
        st = load_state(t["theme_id"])
        ct = catalog.get(t["theme_id"])
        name_kr = ct.name_kr if ct else t["theme_id"]
        full = t["relation"] == "primary"
        edge = edge_map.get(t["theme_id"]) if t["relation"] == "related" else None
        blocks.append(_theme_block(st, t["relation"], float(t["weight"]),
                                   edge, name_kr, full))
        used.append(t)
        if t["theme_id"] == "macro":
            have_macro = True

    # Always inject macro as background backbone (§F3) if not already mapped.
    macro_ct = catalog.get("macro")
    if not have_macro and macro_ct:
        blocks.append(_theme_block(load_state("macro"), "macro", 0.2, None,
                                   macro_ct.name_kr, full=False))
        used.append({"theme_id": "macro", "relation": "macro", "weight": 0.2})

    n_dev = sum(len(b.get("recent_developments", [])) for b in blocks)
    has_content = any(
        b.get("recent_developments") or b.get("horizon_summary")
        or b.get("watchlist") for b in blocks
    )
    return {
        "symbol_id": symbol_id,
        "primary_theme": primary_id,
        "themes": blocks,
        "themes_used": used,
        "data_available": {
            "themes_mapped": bool(themes),
            "has_content": has_content,
            "n_developments": n_dev,
        },
    }


# ── judgment (claude -p applies the rulebook) ───────────────────────────────

# System directive: 4-lens contract + layperson-first output + the rulebook.
_SYSTEM = (
    "너는 개인투자자를 돕는 정성(뉴스·이슈·트렌드) 분석가다. 아래 룰북을 적용해 "
    "한 종목이 속한 테마들의 현재 상태를 종합 평가한다.\n"
    "원칙(반드시 지킬 것):\n"
    "1) 단일 시그널로 단독 결론 금지 — §A 이벤트 분류 + 4렌즈(§B 반영도 → §C 방향·강도 "
    "→ §D 시점 → §E 2차파급)를 결합한다.\n"
    "2) §B 반영도와 §C 서프라이즈를 반드시 교차 — '호재'여도 선반영(mostly_priced)이면 "
    "점수를 깎는다(buy-the-rumor-sell-the-news).\n"
    "3) 선행신호(leading)는 watchlist·second_order에 크게, 점수엔 소폭만 반영한다"
    "(상용화 시차·저확률, §D2/§G5). arXiv 한 편으로 점수를 크게 움직이지 마라.\n"
    "4) 부호는 테마가 아니라 '이 종목 입장'으로 정한다(§C1) — 테마 호재가 종목엔 악재일 수 있다.\n"
    "5) primary 테마 중심, related는 weight·edge_relation만큼, macro는 배경으로만 반영한다(§F).\n"
    "6) drivers와 risks를 둘 다 채운다(확증편향 방지, §G7). 신호가 없으면 중립이다 "
    "— 공백을 악재로 만들지 마라(§F4).\n"
    "7) 가격을 못 보므로 반영도(priced_in)는 정보 확산단계 추정임을 전제한다. 데이터가 "
    "부족하면 confidence를 낮추고 근거에 명시한다(추측 금지, §I).\n"
    "8) 각 신호는 점수에 한 번만 계상한다 — sentiment.trend는 §C2에서만, §G2 하이프·§E4 "
    "리플렉시비티 경고는 점수 직접 감산이 아니라 confidence 하향으로만(§J 단일계상).\n"
    "★ 가장 중요: 사용자는 비전문 개인투자자다. summary_ko·drivers·risks·second_order는 "
    "전문용어 없이 '쉬운 한국어'로 풀어 쓴다.\n\n"
    "반드시 아래 JSON 스키마 1개만 출력한다(설명·코드펜스 금지):\n"
    "{\n"
    '  "score": -1.0~1.0,                 // 정성 종합(음수=부정, 양수=긍정). 방향 × (1−반영도) × 종목귀속\n'
    '  "summary_ko": "비전문가용 한두 문장 결론",\n'
    '  "priced_in": "unpriced|partially_priced|mostly_priced",  // §B 반영도\n'
    '  "time_horizon": "immediate|near|mid|long",               // §D 핵심 동인 대표 시점\n'
    '  "rationale": "2~3문장 근거(왜 이 점수인지)",\n'
    '  "drivers": ["긍정/기회 동인(쉬운 말, 끝에 [lead_lag/시점] 태그 권장)"],\n'
    '  "risks": ["위험 요인(쉬운 말)"],\n'
    '  "second_order": ["2차·3차 파급: 원인 → 결과 (§E)"],\n'
    '  "event_tags": ["이벤트 카테고리(§A): 규제·정책/M&A/실적/기술돌파/수급/공급망/경쟁/소송/거버넌스/기초연구/거시 중"],\n'
    '  "evidence": [{"theme":"battery","point":"...","lead_lag":"leading"}],\n'
    '  "watch": ["미반영·전개중 모니터 대상(state.watchlist 기반)"],\n'
    '  "confidence": 0.0~1.0,             // 데이터 충분성·반영도 추정 신뢰\n'
    '  "methodology": ["적용 룰 ID 예: B2, C2, E2"]\n'
    "}\n\n"
    "── 룰북 ──\n" + _RULES
)


def _neutral(reason: str, *, status: str, watch: Optional[list] = None) -> dict:
    """Verdict for no-mapping / no-content cases — neutral, low confidence, NO
    LLM call (don't burn a claude run on empty input)."""
    return {
        "status": status,
        "score": 0.0,
        "label": "neutral",
        "summary_ko": reason,
        "priced_in": None,
        "time_horizon": None,
        "rationale": reason,
        "drivers": [],
        "risks": [],
        "second_order": [],
        "event_tags": [],
        "evidence": [],
        "watch": (watch or [])[:6],
        "confidence": 0.0,
        "methodology": [],
    }


def _coerce_enum(v, allowed: tuple) -> Optional[str]:
    return v if v in allowed else None


def _judge(symbol_id: str, name: str, inputs: dict, *,
           backend=None, timeout_s: float = 280) -> dict:
    """Apply news_rules.md (claude -p) to the assembled theme states.

    Short-circuits to a neutral, low-confidence verdict WITHOUT calling the
    model when the symbol has no theme mapping or no qualitative content, so
    the button never burns a claude run on empty data (§F4 / §H10)."""
    avail = inputs.get("data_available", {})
    if not avail.get("themes_mapped"):
        return _neutral("이 종목에 매핑된 테마가 없습니다. 테마 매핑 후 다시 시도하세요.",
                        status="insufficient_data")
    if not avail.get("has_content"):
        # No standing trend and no recent signal anywhere → neutral, but still
        # surface any watchlist (a future catalyst is not 'no signal', §H14).
        watch: list = []
        for b in inputs.get("themes", []):
            watch += b.get("watchlist", [])
        return _neutral("아직 분석할 정성 정보가 충분치 않습니다(중립). 매일 새벽 수집 후 다시 시도하세요.",
                        status="insufficient_data", watch=watch)

    backend = backend or ClaudeCliBackend(model=SLOW_MODEL)
    user = json.dumps({
        "symbol_id": symbol_id, "name": name,
        "primary_theme": inputs.get("primary_theme"),
        "themes": inputs.get("themes", []),
    }, ensure_ascii=False, default=str)
    raw = backend.call(_SYSTEM, user, timeout_s=timeout_s, model=SLOW_MODEL)
    data = _parse_json(raw)

    score = clamp_score(float(data.get("score", 0)))
    conf = clamp_confidence(data.get("confidence", 0.5))
    return {
        "status": "ok",
        "score": round(score, 4),
        "label": label_from_score(score),
        "summary_ko": data.get("summary_ko", ""),
        "priced_in": _coerce_enum(data.get("priced_in"), _PRICED_IN),
        "time_horizon": _coerce_enum(data.get("time_horizon"), _HORIZONS),
        "rationale": data.get("rationale", ""),
        "drivers": (data.get("drivers") or [])[:6],
        "risks": (data.get("risks") or [])[:6],
        "second_order": (data.get("second_order") or [])[:6],
        "event_tags": (data.get("event_tags") or [])[:8],
        "evidence": (data.get("evidence") or [])[:8],
        "watch": (data.get("watch") or [])[:6],
        "confidence": conf,
        "methodology": (data.get("methodology") or [])[:12],
    }


def evaluate_symbol(symbol_id: str, *, name: Optional[str] = None,
                    catalog: Optional[Catalog] = None, conn=None,
                    date: Optional[str] = None, backend=None,
                    timeout_s: float = 280) -> dict:
    """Assemble inputs → judge (rulebook via claude -p) → persist. Returns dict."""
    catalog = catalog or load_catalog()
    date = date or datetime.now(_KST).strftime("%Y-%m-%d")
    name = name or symbol_id

    inputs = assemble_inputs(symbol_id, catalog=catalog, conn=conn)
    verdict = _judge(symbol_id, name, inputs, backend=backend, timeout_s=timeout_s)

    result = {
        "symbol_id": symbol_id,
        "kind": "news",
        "date": date,
        "name": name,
        **verdict,
        "themes_used": inputs.get("themes_used", []),
        "generated_at": datetime.now(_KST).isoformat(),
    }
    return persist("news", symbol_id, date, result)
