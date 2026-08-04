"""Symbol -> theme mapping (L1).

Two paths:
  - ETF:         deterministic name-keyword rule (``map_etf_by_name``)
  - Individual:  one-shot Claude CLI tagging (``tag_stock_by_llm``)

Called from the holdings API when a newly-added symbol has no mapping yet.

Results persist to portfolio.db ``holding_themes`` (relation primary|related).
"""
from __future__ import annotations

from typing import Optional

from .catalog import Catalog, Theme
from ..llm_backend import ClaudeCliBackend, SLOW_MODEL, _parse_json


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
    for t in catalog.active_themes():  # active_themes excludes shadow
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
        for t in catalog.active_themes() if not t.always_on
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


def held_primary_theme_ids(conn) -> set[str]:
    """theme_ids that at least one currently-held symbol maps to as ``primary``.

    Drives the daily collection set: a theme is collected only while some
    holding names it as its core business (§ holdings-driven activation).
    Related-only mappings do NOT keep a theme alive — most related themes are
    already some other holding's primary, so this stays coverage-safe.
    """
    rows = conn.execute(
        "SELECT DISTINCT ht.theme_id FROM holding_themes ht "
        "JOIN holdings h ON h.symbol_id = ht.symbol_id "
        "WHERE ht.relation = 'primary'"
    ).fetchall()
    return {r["theme_id"] for r in rows}


def has_holding_themes(symbol_id: str, conn) -> bool:
    """True if the symbol is already mapped — the guard that stops a re-add
    from burning another LLM tagging call."""
    return conn.execute(
        "SELECT 1 FROM holding_themes WHERE symbol_id = ? LIMIT 1",
        (symbol_id,),
    ).fetchone() is not None


def drop_holding_themes(symbol_id: str, conn) -> None:
    """Forget a symbol's theme mapping — called once no account holds it, so
    themes with no remaining holder stop collecting on the next daily_job."""
    conn.execute("DELETE FROM holding_themes WHERE symbol_id = ?", (symbol_id,))
    conn.commit()


def live_themes(catalog: Catalog) -> list[Theme]:
    """The themes this portfolio actually runs on (§ holdings-driven activation).

    A theme is live iff some current holding maps to it as ``primary``, plus
    the always-on backbones (macro). This — not the static ``active`` flag in
    themes.json, which stays the taxonomy of *taggable* themes — is the single
    source of truth for both nightly collection and the landing feed, so adding
    or removing a holding grows/shrinks the news footprint by itself.
    """
    from ...portfolio.store.db import connect

    with connect() as c:
        held = held_primary_theme_ids(c)
    return [t for t in catalog.themes.values()
            if not t.shadow and (t.always_on or t.theme_id in held)]


def save_holding_themes(symbol_id: str, rows: list[dict], conn,
                        updated_at: Optional[str] = None) -> None:
    """Replace holding_themes for one symbol (idempotent upsert by delete+insert)."""
    import time
    ts = updated_at or time.strftime("%Y-%m-%dT%H:%M:%S")
    conn.execute("DELETE FROM holding_themes WHERE symbol_id = ?", (symbol_id,))
    conn.executemany(
        "INSERT INTO holding_themes "
        "(symbol_id, theme_id, relation, weight, source, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(symbol_id, r["theme_id"], r["relation"], r["weight"],
          r.get("source", "auto"), ts) for r in rows],
    )
    conn.commit()
