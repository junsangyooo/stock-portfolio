"""Symbol -> theme mapping (L1).

Two paths (see docs/qualitative-analysis-design.md §2):
  - ETF:         deterministic name-keyword rule (``map_etf_by_name``)
  - Individual:  one-shot Claude CLI tagging (``tag_stock_by_llm`` / batch),
                 human-reviewed before commit.

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


def tag_stocks_batch(stocks: list[tuple[str, str, str]], catalog: Catalog,
                     backend=None, timeout_s: float = 240) -> dict:
    """Tag many individual stocks in ONE LLM call. Keyed by code.

    `stocks`: list of (code, name, market). Returns
    {code: {"primary": [...], "related": [...]}} with invalid theme_ids dropped.
    """
    backend = backend or ClaudeCliBackend(model=SLOW_MODEL)
    primary_choices = "\n".join(
        f"- {t.theme_id}: {t.name_kr} — {t.definition}"
        for t in catalog.active_themes() if not t.always_on
    )
    related_choices = ", ".join(sorted(catalog.themes))
    listing = "\n".join(f"- {code} {name} ({mkt})" for code, name, mkt in stocks)
    user = (
        f"다음 종목들을 각각 테마로 분류하라.\n\n[종목]\n{listing}\n\n"
        f"[primary 후보 — 핵심 사업 분야]\n{primary_choices}\n\n"
        f"[related 후보 — 연관/공급망, 위 + 그림자 테마 모두 가능]\n{related_choices}\n\n"
        "출력(JSON, code를 키로):\n"
        '{"005930": {"primary": ["theme_id"], "related": ["theme_id"]}, ...}\n'
        "- primary 1~2개, related 0~3개. 목록의 theme_id만. macro 금지(전 종목 공통)."
    )
    raw = backend.call(_SYSTEM, user, timeout_s=timeout_s, model=SLOW_MODEL)
    data = _parse_json(raw)
    valid = set(catalog.themes)
    out: dict = {}
    for code, _, _ in stocks:
        d = data.get(code, {}) or {}
        primary = [x for x in d.get("primary", []) if x in valid]
        related = [x for x in d.get("related", []) if x in valid and x not in primary]
        out[code] = {"primary": primary, "related": related}
    return out


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
