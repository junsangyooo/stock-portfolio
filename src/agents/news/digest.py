"""L4 step 1 — daily theme digest.

Compresses one day's raw signals (jsonl) into a small set of key items via a
single LLM call. To keep urls/sources exact, raw items are indexed and the LLM
returns only {idx, summary, importance, novelty}; canonical fields are restored
from the originals. See docs/qualitative-analysis-design.md §4.1.
"""
from __future__ import annotations

import json

from ...core.paths import signals_news_path, analysis_news_daily_path
from ..llm_backend import ClaudeCliBackend, FAST_MODEL, _parse_json

_SUMMARY_TRUNC = 400  # chars of raw summary fed to the LLM

# Cost control: dedup/curation is mechanical → Haiku is enough (was Sonnet).
_MODEL = FAST_MODEL

# The LLM keeps only 5~8 items regardless, so feeding all raw signals (a busy
# theme can hit ~100/day) just burns tokens. Pre-rank rule-based and cap.
_MAX_DIGEST_ITEMS = 40
_LEAD_LAG_RANK = {"leading": 3, "backbone": 2, "coincident": 2, "lagging": 1}


def _rank_and_cap(items: list[dict], cap: int = _MAX_DIGEST_ITEMS) -> list[dict]:
    """Keep the top `cap` raw items by (lead_lag priority, recency).

    Deterministic pre-filter before the LLM call. Leading/research signals win
    over lagging news; ties broken by fetched_at. Returns items unchanged when
    already under the cap.
    """
    if len(items) <= cap:
        return items
    ranked = sorted(
        items,
        key=lambda r: (_LEAD_LAG_RANK.get(r.get("lead_lag"), 1),
                       r.get("fetched_at") or ""),
        reverse=True,
    )
    return ranked[:cap]


def _safe_int(v, default: int = 3) -> int:
    """Coerce an LLM-returned value to int, tolerating lists/strings/None.

    The model occasionally emits importance as [4] or "4" instead of 4;
    a bare int() then raises and kills the whole theme's digest.
    """
    if isinstance(v, (list, tuple)):
        v = v[0] if v else default
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def load_signals_for_date(theme_id: str, date: str) -> list[dict]:
    """Read raw records for a theme whose fetched_at falls on `date` (YYYY-MM-DD)."""
    yyyymm = date.replace("-", "")[:6]
    path = signals_news_path(theme_id, yyyymm)
    out: list[dict] = []
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (rec.get("fetched_at") or "").startswith(date):
            out.append(rec)
    return out


_SYSTEM = (
    "너는 투자 정보 큐레이터다. 하루치 원시 시그널에서 중복을 제거하고 "
    "투자 판단에 중요한 핵심만 선별한다. JSON만 출력한다."
)


def _build_prompt(theme_name: str, items: list[dict]) -> str:
    lines = []
    for i, r in enumerate(items):
        summ = (r.get("summary") or "")[:_SUMMARY_TRUNC]
        lines.append(
            f"[{i}] ({r.get('lead_lag')}/{r.get('source_id')}) "
            f"{r.get('title')}\n    {summ}"
        )
    listing = "\n".join(lines)
    return (
        f"테마: {theme_name}\n원시 시그널 {len(items)}건:\n\n{listing}\n\n"
        "작업: 근접 중복을 병합하고, 투자 판단에 중요한 5~8건만 선별하라.\n"
        "각 선별 항목에 한국어 한 문장 요약·중요도(1~5)·신규성(true/false)을 붙여라.\n"
        "출력(JSON):\n"
        '{"selected": [{"idx": 0, "summary": "한문장", "importance": 4, "novelty": true}], '
        '"dropped_dup": 3}\n'
        "- idx는 위 번호. 중요도 높은 순. 최대 8건."
    )


def make_daily_digest(theme_id: str, theme_name: str, raw_items: list[dict],
                      date: str, backend=None, timeout_s: float = 300) -> dict:
    """Build a daily digest dict from that day's raw items. Empty-safe."""
    if not raw_items:
        return {"theme": theme_id, "date": date, "n_raw": 0,
                "items": [], "dropped_dup": 0}
    work = _rank_and_cap(raw_items)  # idx below is relative to `work`
    backend = backend or ClaudeCliBackend(model=_MODEL)
    raw = backend.call(_SYSTEM, _build_prompt(theme_name, work),
                       timeout_s=timeout_s, model=_MODEL)
    data = _parse_json(raw)
    items = []
    for sel in data.get("selected", []):
        idx = sel.get("idx")
        if not isinstance(idx, int) or not (0 <= idx < len(work)):
            continue
        src = work[idx]
        items.append({
            "title": src.get("title"),
            "url": src.get("url"),
            "source_id": src.get("source_id"),
            "lead_lag": src.get("lead_lag"),
            "summary": str(sel.get("summary", ""))[:300],
            "importance": _safe_int(sel.get("importance"), 3),
            "novelty": bool(sel.get("novelty", False)),
        })
    return {"theme": theme_id, "date": date, "n_raw": len(raw_items),
            "items": items, "dropped_dup": int(data.get("dropped_dup", 0))}


def write_daily_digest(digest: dict) -> str:
    path = analysis_news_daily_path(digest["theme"], digest["date"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(digest, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    return str(path)
