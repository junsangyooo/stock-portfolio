"""Symbol -> theme mapping (L1).

Two paths:
  - ETF:         deterministic name-keyword rule (``map_etf_by_name``)
  - Individual:  one-shot Claude CLI tagging (``tag_stock_by_llm``)

Called from the holdings API when a newly-added symbol has no mapping yet.

Results persist to market.db ``symbol_themes`` (relation primary|related) —
the symbol book, so both account books read the same mapping.
"""
from __future__ import annotations

import logging
from typing import Optional

from .catalog import Catalog, Theme
from ..llm_backend import ClaudeCliBackend, SLOW_MODEL, _parse_json

log = logging.getLogger(__name__)


# ── ETF: deterministic name rule ─────────────────────────────────────────────
def _norm(s: str) -> str:
    return s.replace(" ", "").lower()


def map_etf_by_name(name: str, catalog: Catalog) -> list[str]:
    """Map an ETF display name to active (non-shadow) theme_ids by keyword
    substring match. Returns [] when nothing matches (needs human review).

    Only Korean-list keywords are used for matching — they already include the
    relevant English/index tokens (AI, ESS, HBM, S&P500, ...) while avoiding
    over-broad pure-English keywords.
    """
    norm = _norm(name)
    hits: list[str] = []
    for t in catalog.themes.values():   # macro included: index ETFs belong to it
        for kw in t.keywords.get("ko", []):
            if kw and _norm(kw) in norm:
                hits.append(t.theme_id)
                break
    return hits


# ── Individual stock: LLM tagging ────────────────────────────────────────────
_SYSTEM = (
    "너는 한국/미국 주식의 사업 분야를 테마로 분류하는 애널리스트다. "
    "주어진 테마 목록 안에서만 고른다. JSON만 출력한다."
)


def _build_user_prompt(name: str, market: str, catalog: Catalog) -> str:
    primary_choices = "\n".join(
        f"- {t.theme_id}: {t.name_kr} — {t.definition}"
        for t in catalog.primary_candidates()
    )
    related_choices = ", ".join(sorted(catalog.themes))
    return (
        f"종목: {name} (시장: {market})\n\n"
        f"[primary 후보 — 이 종목의 핵심 사업 분야]\n{primary_choices}\n\n"
        f"[related 후보 — 연관/공급망 분야, 위 + 그림자 테마 모두 가능]\n"
        f"{related_choices}\n\n"
        "출력 형식(JSON):\n"
        '{"primary": ["theme_id", ...], "related": ["theme_id", ...]}\n'
        "- primary: 1~2개 (핵심 사업). related: 0~3개 (연관 분야).\n"
        "- 반드시 위 목록의 theme_id만 사용. macro는 고르지 말 것(전 종목 공통)."
    )


def tag_stock_by_llm(name: str, market: str, catalog: Catalog,
                     backend=None, timeout_s: float = 60) -> dict:
    """Tag one stock via the LLM. Filters out any theme_id not in the catalog.

    `backend` must expose ``call(system, user, timeout_s, model)``; defaults to
    ClaudeCliBackend. Returns {"primary": [...], "related": [...]}.
    """
    backend = backend or ClaudeCliBackend(model=SLOW_MODEL)
    raw = backend.call(_SYSTEM, _build_user_prompt(name, market, catalog),
                       timeout_s=timeout_s, model=SLOW_MODEL)
    data = _parse_json(raw)
    valid = set(catalog.themes)
    primary = [x for x in data.get("primary", []) if x in valid]
    related = [x for x in data.get("related", []) if x in valid and x not in primary]
    return {"primary": primary, "related": related}


# ── Persistence ──────────────────────────────────────────────────────────────
def _edge_weight(theme: Optional[Theme], target: str) -> float:
    """Weight for a related edge if declared on the primary theme, else 0.5."""
    if theme:
        for edge in theme.related:
            if edge.get("theme") == target:
                return float(edge.get("weight", 0.5))
    return 0.5


def resolve_symbol_themes(name: str, market: str, is_etf: bool,
                          catalog: Catalog, backend=None) -> list[dict]:
    """Return rows [{theme_id, relation, weight, source}] for one symbol.

    ETF -> name rule (source=auto). Individual -> LLM (source=auto, review).
    """
    rows: list[dict] = []
    if is_etf:
        for tid in map_etf_by_name(name, catalog):
            rows.append({"theme_id": tid, "relation": "primary",
                         "weight": 1.0, "source": "auto"})
        return rows

    tagged = tag_stock_by_llm(name, market, catalog, backend=backend)
    prim_themes = [catalog.get(t) for t in tagged["primary"]]
    for tid in tagged["primary"]:
        rows.append({"theme_id": tid, "relation": "primary",
                     "weight": 1.0, "source": "auto"})
    for tid in tagged["related"]:
        w = max((_edge_weight(pt, tid) for pt in prim_themes if pt), default=0.5)
        rows.append({"theme_id": tid, "relation": "related",
                     "weight": w, "source": "auto"})
    return rows


def ensure_theme_mapping(symbol_id: str) -> None:
    """Auto-tag a newly-tracked symbol into themes. Best-effort, never raises.

    Skips if already mapped. ETF -> deterministic name rule, individual -> one
    LLM tag. Collection itself does not depend on this — every field is always
    collected — but the mapping is what connects a symbol to its field's news.

    Lives here, not in a router, because both books call it: portfolio when a
    holding is added, quant when a position opens or a symbol is watched. The
    mapping itself is stored per-symbol in market.db, so neither book owns it.
    """
    from ...core.paths import split_symbol
    from ...market.store import theme_store
    from ...universe import search as uni_search
    from .catalog import load_catalog
    try:
        if theme_store.has(symbol_id):
            return  # already mapped — don't burn an LLM call
        rec = uni_search.get(symbol_id) or {}
        name = rec.get("name_kr") or rec.get("name_en") or symbol_id
        market, _ = split_symbol(symbol_id)
        rows = resolve_symbol_themes(name, rec.get("market") or market,
                                     bool(rec.get("is_etf")), load_catalog())
        if not rows:
            return
        theme_store.save(symbol_id, rows)
    except Exception as e:  # noqa: BLE001 — background, must not surface
        log.warning("theme auto-map failed for %s: %s", symbol_id, e)
        try:
            from ...core.notify import discord
            discord.send(f"\u26a0\ufe0f theme auto-map failed for {symbol_id}: {str(e)[:200]}")
        except Exception:  # noqa: BLE001
            pass
