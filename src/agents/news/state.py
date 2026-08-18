"""L4 step 2 — rolling theme state (merge + time-decay).

The state is the ONLY input to stock evaluation (raw is never re-read), so it
must stay a roughly constant size regardless of how long signals accumulate
(fixes Signalee's unbounded-cluster flaw). Mechanism:

  - recent_developments: rule-based time-decay (keep last `horizon_days`,
    cap by weight) — NO LLM, deterministic.
  - horizon_summary / watchlist / sentiment / last_shift: LLM rewrite given
    previous state + today's developments (slow-moving long memory).

See docs/qualitative-analysis-design.md §4.2.
"""
from __future__ import annotations

import json
from datetime import date as _date, datetime

from ...core.kst import KST as _KST
from typing import Optional

from ...core.paths import analysis_news_state_path
from ..llm_backend import SLOW_MODEL, local_or_claude, _parse_json

_HORIZON_DAYS = 7
_RECENT_CAP = 15
# Cost control: the slow-moving fields only need a rewrite when today actually
# brought a material development. Quiet days do the deterministic decay only and
# keep the prior summary/sentiment — a busy theme still updates daily, a quiet
# one naturally drops to ~2-3x/week. importance>=4 -> weight>=0.8.
_FRESH_WEIGHT = 0.8


def empty_state(theme_id: str) -> dict:
    return {
        "theme_id": theme_id,
        "updated": None,
        "horizon_summary": "",
        "recent_developments": [],
        "watchlist": [],
        "sentiment": {"score": 0.0, "trend": "flat"},
        "last_shift": "",
    }


def load_state(theme_id: str) -> dict:
    path = analysis_news_state_path(theme_id)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return empty_state(theme_id)


def decay_recent(developments: list[dict], asof: str,
                 horizon_days: int = _HORIZON_DAYS,
                 cap: int = _RECENT_CAP) -> list[dict]:
    """Keep developments within `horizon_days` of `asof`, then cap by weight.

    Pure / deterministic. Each development needs 'date' (YYYY-MM-DD) and
    'weight'. Out-of-window or unparseable-date items are dropped.
    """
    try:
        asof_d = _date.fromisoformat(asof)
    except ValueError:
        return developments[:cap]
    kept = []
    for d in developments:
        try:
            dd = _date.fromisoformat((d.get("date") or "")[:10])
        except ValueError:
            continue
        if (asof_d - dd).days <= horizon_days and dd <= asof_d:
            kept.append(d)
    kept.sort(key=lambda x: (x.get("weight", 0), x.get("date", "")), reverse=True)
    return kept[:cap]


def _dedup_developments(devs: list[dict]) -> list[dict]:
    """Drop duplicate developments by url (fallback: point). Keeps first seen."""
    seen: set[str] = set()
    out: list[dict] = []
    for d in devs:
        k = (d.get("url") or "").strip() or ("p:" + (d.get("point") or "").strip())
        if k in seen:
            continue
        seen.add(k)
        out.append(d)
    return out


def _digest_to_developments(digest: dict) -> list[dict]:
    out = []
    for it in digest.get("items", []):
        out.append({
            "date": digest.get("date"),
            "point": it.get("summary") or it.get("title"),
            "lead_lag": it.get("lead_lag"),
            "weight": round(int(it.get("importance", 3)) / 5.0, 2),
            "novelty": bool(it.get("novelty", False)),  # kept for evaluate §B (priced-in)
            "url": it.get("url"),
        })
    return out


_SYSTEM = (
    "너는 특정 산업 테마의 상태를 추적하는 애널리스트다. 기존 상태와 오늘의 "
    "전개를 받아 장기추세 요약을 갱신한다. 선행신호(leading)를 후행(lagging)보다 "
    "중시한다. JSON만 출력한다."
)


def _build_prompt(theme_name: str, prev: dict, recent: list[dict]) -> str:
    recent_lines = "\n".join(
        f"- {d.get('date')} [{d.get('lead_lag')},w{d.get('weight')}] {d.get('point')}"
        for d in recent
    ) or "(없음)"
    return (
        f"테마: {theme_name}\n\n"
        f"[기존 장기추세 요약]\n{prev.get('horizon_summary') or '(없음)'}\n\n"
        f"[기존 watchlist]\n{', '.join(prev.get('watchlist', [])) or '(없음)'}\n\n"
        f"[기존 sentiment]\n{json.dumps(prev.get('sentiment', {}), ensure_ascii=False)}\n\n"
        f"[최근 전개 (시간감쇠 후)]\n{recent_lines}\n\n"
        "작업:\n"
        "- horizon_summary: 장기추세를 2~3문장으로 갱신(천천히 변함, 새 정보 반영).\n"
        "- watchlist: 전개중/예정 이슈 최대 5개.\n"
        "- sentiment: score(-1~1), trend(improving|flat|deteriorating).\n"
        "- last_shift: 센티먼트 방향 전환이 있었으면 'YYYY-MM-DD: 중립→약세 (이유)', "
        "없으면 기존 값 유지.\n"
        "출력(JSON):\n"
        '{"horizon_summary": "...", "watchlist": ["..."], '
        '"sentiment": {"score": 0.0, "trend": "flat"}, "last_shift": "..."}'
    )


def update_state(theme_id: str, theme_name: str, digest: dict,
                 prev_state: Optional[dict] = None, date: Optional[str] = None,
                 backend=None, timeout_s: float = 300) -> dict:
    """Merge a daily digest into the rolling state. Returns the new state."""
    prev = prev_state if prev_state is not None else load_state(theme_id)
    date = date or digest.get("date") or datetime.now(_KST).strftime("%Y-%m-%d")

    merged = prev.get("recent_developments", []) + _digest_to_developments(digest)
    merged = _dedup_developments(merged)  # idempotent re-runs (same-day rerun safe)
    recent = decay_recent(merged, asof=date)

    new = dict(prev)
    new["theme_id"] = theme_id
    new["updated"] = date
    new["recent_developments"] = recent

    # LLM rewrite of the slow-moving fields. Gated on a material signal today:
    # skip on quiet days (decay-only) unless this is the first build. `new` was
    # copied from `prev`, so skipping preserves the prior slow fields verbatim.
    today_devs = _digest_to_developments(digest)
    fresh_signal = any(d.get("weight", 0) >= _FRESH_WEIGHT or d.get("novelty")
                       for d in today_devs)
    first_build = not prev.get("horizon_summary")
    if recent and (fresh_signal or first_build):
        prompt = _build_prompt(theme_name, prev, recent)
        if backend is not None:                   # tests inject their own
            raw = backend.call(_SYSTEM, prompt, timeout_s=timeout_s,
                               model=SLOW_MODEL)
        else:
            raw, _ = local_or_claude(_SYSTEM, prompt, claude_model=SLOW_MODEL,
                                     stage=f"state:{theme_id}",
                                     claude_timeout_s=timeout_s)
        data = _parse_json(raw)
        new["horizon_summary"] = data.get("horizon_summary", prev.get("horizon_summary", ""))
        new["watchlist"] = data.get("watchlist", [])[:5]
        sent = data.get("sentiment", {}) or {}
        new["sentiment"] = {
            "score": max(-1.0, min(1.0, float(sent.get("score", 0)))),
            "trend": sent.get("trend", "flat"),
        }
        new["last_shift"] = data.get("last_shift", prev.get("last_shift", ""))
    return new


def write_state(state: dict) -> str:
    path = analysis_news_state_path(state["theme_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return str(path)
