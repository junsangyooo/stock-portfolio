"""Screen invariants, enforced from source the way test_quant_api.py enforces
require_quant: the two products never reference each other, and each public
Host serves only its own pages (ops notes §2.2 — same app, same nginx block;
only the Host header and host-only cookies separate them).
"""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.dashboard_web import server
from src.quant import db as qdb

STATIC = Path(server.__file__).resolve().parent / "static"
PORTFOLIO_HOST = "portfolio.jsyoo.dev"
QUANT_HOST = "quant.jsyoo.dev"


@pytest.fixture
def client(tmp_path, monkeypatch):
    # /q names the quant account from quant.db — point the book at a tmp file.
    monkeypatch.setattr(qdb, "QUANT_DB_PATH", tmp_path / "quant.db")
    monkeypatch.setattr(qdb, "_initialized", False)
    qdb.forget_account_id()
    return TestClient(server.app)


def _hits(name: str, patterns: list[str]) -> list[str]:
    lines = (STATIC / name).read_text().splitlines()
    return [f"{name}:{i}: {line.strip()}"
            for i, line in enumerate(lines, 1)
            if any(re.search(p, line, re.IGNORECASE) for p in patterns)]


def test_no_crosslinks_between_products():
    """Case-insensitive on purpose: with case respected a wordmark like
    '◆ QUANT-LAB' on the portfolio screen passes untouched."""
    to_quant = [r"/q\b", r"quant", r"pdash_q"]        # 'quant' covers /api/quant
    to_portfolio = [r"/a/", r"/api/accounts", r"pdash_a"]
    bad = (_hits("landing.html", to_quant)
           + _hits("account.html", to_quant)
           + _hits("quant.html", to_portfolio))
    assert not bad, "cross-product references:\n" + "\n".join(bad)


def test_host_gate_on_page_routes(client):
    """404, never a redirect — a redirect is itself a path between the
    products. Deny-list: only the two public hosts are refused each other's
    page, so local and IP access (laptop, Oracle shell) keeps working."""
    def get(path, host):
        return client.get(path, headers={"host": host}, follow_redirects=False)

    assert get("/a/x", QUANT_HOST).status_code == 404
    assert get("/q", PORTFOLIO_HOST).status_code == 404
    assert get("/q", QUANT_HOST).status_code == 200
    assert get("/a/x", PORTFOLIO_HOST).status_code == 200
    for host in ("127.0.0.1:8888", "localhost:8888"):
        assert get("/q", host).status_code == 200, host
        assert get("/a/x", host).status_code == 200, host


def test_quant_page_not_served_on_portfolio_host(client):
    """The account id is injected by _serve_quant() only; its absence on the
    portfolio host is the check (a string search on the id would match the
    hardcoded fallback in account.html)."""
    tag = "window.QUANT_ACCOUNT"
    for path in ("/", "/q"):
        r = client.get(path, headers={"host": PORTFOLIO_HOST})
        assert tag not in r.text, path
    assert tag in client.get("/q", headers={"host": QUANT_HOST}).text


# ── U2: shared floor ─────────────────────────────────────────────────────
SHARED_JS = STATIC / "shared.js"
SHARED_CSS = STATIC / "shared.css"
THEME = STATIC / "theme.json"
NEWS_THEMES = Path(server.__file__).resolve().parents[1] / "research" / "news" / "themes.json"
SCREEN_FILES = ("landing.html", "account.html", "quant.html", "shared.js", "shared.css")

# Every name the injected fragment exports (ui-02-architecture §4). Each must
# be defined exactly once across the fragment and the three pages.
SHARED_NAMES = (
    "el", "clear", "svgIco", "timeAgo", "kstClock",
    "money", "ccyMoney", "pct", "signCls",
    "designColor", "designRgba", "newsThemeColor", "newsThemeIcon",
    "srcName", "newsCard", "newsGrid", "toast", "api",
    # U4: the card grammar both pages draw with
    "drawChart", "fmtBarDate", "drawCurve", "LABEL_KO", "freshInfo", "verdictBadge",
)

# A colour literal in a VALUE position: after ':' / '=' / '(' / ',' or an
# opening quote. Anchoring on the value keeps '#account-tabs' (a CSS id whose
# first letters happen to be hex) and '#i-lock' (a sprite reference) out —
# the negative lookahead refuses a hex run that continues into a word.
_HEX_VALUE = re.compile(r"[:=(,'\"`]\s*#[0-9a-fA-F]{3,8}(?![\w-])")
_RAW_RGBA = re.compile(r"\brgba?\(\s*\d")


def test_no_color_literals():
    bad = []
    for name in SCREEN_FILES:
        for i, line in enumerate((STATIC / name).read_text().splitlines(), 1):
            if _HEX_VALUE.search(line) or _RAW_RGBA.search(line):
                bad.append(f"{name}:{i}: {line.strip()}")
    assert not bad, "colour literals outside theme.json:\n" + "\n".join(bad)


def test_every_news_theme_has_a_token():
    """themes.json is the taxonomy, theme.json the design tokens — two files
    that can drift. Drift is allowed to exist but not to be silent."""
    import json
    ids = [t["theme_id"] for t in json.loads(NEWS_THEMES.read_text())["themes"]]
    tokens = json.loads(THEME.read_text())["themes"]
    missing = [i for i in ids if i not in tokens]
    assert not missing, f"news themes without a colour/icon token: {missing}"
    for tid in ids:
        assert re.fullmatch(r"#[0-9a-fA-F]{6}", tokens[tid]["color"]), tid
        assert tokens[tid]["icon"], tid


def test_every_theme_icon_in_sprite():
    """A <use href="#i-x"> with no <symbol> renders as nothing, silently."""
    import json
    sprite = SHARED_JS.read_text()
    icons = {v["icon"] for v in json.loads(THEME.read_text())["themes"].values()}
    icons |= {"newspaper", "lock"}   # fallback icon + the account-tab lock
    missing = sorted(i for i in icons if f'id="i-{i}"' not in sprite)
    assert not missing, f"icons referenced but absent from the sprite: {missing}"


def test_shared_names_defined_once():
    dup, none = [], []
    for name in SHARED_NAMES:
        pat = re.compile(
            rf"^(?:async\s+)?function\s+{name}\s*\(|^(?:const|let|var)\s+{name}\s*=",
            re.MULTILINE)
        hits = {f: len(pat.findall((STATIC / f).read_text()))
                for f in SCREEN_FILES if f.endswith((".js", ".html"))}
        total = sum(hits.values())
        if total > 1:
            dup.append(f"{name}: {hits}")
        elif total == 0:
            none.append(name)
        assert re.search(rf"\b{name}\b", SHARED_JS.read_text()), f"{name} not in shared.js"
    assert not dup, "shared names defined more than once:\n" + "\n".join(dup)
    assert not none, f"shared names never defined: {none}"
    exported = re.search(r"window\.S\s*=\s*\{([^}]*)\}", SHARED_JS.read_text())
    assert exported, "shared.js must export window.S = { ... }"
    names = {n.strip() for n in exported.group(1).split(",") if n.strip()}
    assert set(SHARED_NAMES) <= names, set(SHARED_NAMES) - names


def test_shared_fragments_injected_in_order(client):
    """<head>: theme tokens → shared.css → shared.js → the page's own
    <style>/<script>. CSS coming later is cascade intent; JS coming later is
    the reason the fragment lives under one namespace (S.*)."""
    for path, host in (("/", PORTFOLIO_HOST), ("/a/x", PORTFOLIO_HOST), ("/q", QUANT_HOST)):
        body = client.get(path, headers={"host": host}).text
        marks = ['id="theme-vars"', "window.__THEME_TOKENS", 'id="shared-css"',
                 'id="shared-js"', "@import url("]
        pos = [body.find(m) for m in marks]
        assert all(p >= 0 for p in pos), (path, dict(zip(marks, pos)))
        assert pos == sorted(pos), (path, dict(zip(marks, pos)))
    src = Path(server.__file__).read_text()
    assert "app.mount(" not in src, "no /static mount — fragments are injected"


def test_shared_names_not_called_bare():
    """A page reaches a shared helper either through `S.name` or by
    destructuring it from S. A bare `name(` with neither is a ReferenceError
    at render time (landing's 오늘의 핵심 broke this way)."""
    bad = []
    for f in SCREEN_FILES:
        if not f.endswith(".html"):
            continue
        src = (STATIC / f).read_text()
        pulled = set()
        for m in re.finditer(r"const\s*\{([^}]*)\}\s*=\s*S\b", src):
            pulled |= {n.strip() for n in m.group(1).split(",") if n.strip()}
        for name in SHARED_NAMES:
            if name in pulled:
                continue
            if re.search(rf"(?:function\s+{name}\s*\(|(?:const|let|var)\s+{name}\s*=)", src):
                continue  # page-local definition (e.g. a closure-scoped api())
            for i, line in enumerate(src.splitlines(), 1):
                if re.search(rf"(?<![\w.$]){name}\s*\(", line):
                    bad.append(f"{f}:{i}: {line.strip()}")
    assert not bad, "shared helpers called without S. or destructure:\n" + "\n".join(bad)


# ── U3: portfolio account page ───────────────────────────────────────────
ACCOUNT = STATIC / "account.html"


def test_account_page_has_one_analysis_run_trigger():
    """The only thing that starts an analysis job is the modal header's
    「다시 분석」. Per-domain runs are gone from the server (B7), and a second
    button in an empty/error state re-created the double-run bug."""
    src = ACCOUNT.read_text()
    assert "runChartAnalysis" not in src
    assert "watchChartJob" not in src
    assert len(re.findall(r"API\.runAnalysis\(", src)) == 1
    assert len(re.findall(r"/run'", src)) == 1
    assert "['다시 시도']" not in src and "전체 분석 실행" not in src


def test_account_page_reads_current_engine_schema():
    """Keys the 종합 renderer reads must exist in what engine.normalize and
    engine._facts_only actually emit. ANALYSIS_BODY_KEYS in account.html is
    the one list; this test resolves every dotted path against both shapes."""
    from src.research.analyze import engine
    src = ACCOUNT.read_text()
    for stale in ("crossItems", "disagreement_note_ko", "levels.supports",
                  "levels.resistances", "'junisa'", "10~14"):
        assert stale not in src, stale
    m = re.search(r"const ANALYSIS_BODY_KEYS\s*=\s*\[([^\]]*)\]", src)
    assert m, "ANALYSIS_BODY_KEYS missing from account.html"
    paths = re.findall(r"'([\w.]+)'", m.group(1))
    assert paths
    env = {"symbol": {"symbol_id": "KR:005930", "name": "삼성전자", "market": "KOSPI"},
           "asof": "2026-09-04", "domains": [], "available": []}
    shapes = {"ok": engine.normalize({}, env), "facts_only": engine._facts_only(env, "x")}
    for path in paths:
        for kind, body in shapes.items():
            cur = body
            for seg in path.split("."):
                assert isinstance(cur, dict) and seg in cur, f"{kind} lacks {path}"
                cur = cur[seg]
        # the renderer really reads that leaf (not just the list)
        leaf = path.split(".")[-1]
        assert re.search(rf"[.'\"]{leaf}\b", src.split("const ANALYSIS_BODY_KEYS")[1]), leaf


def test_account_page_trade_flow_uses_trades_route():
    src = ACCOUNT.read_text()
    assert "/trades'" in src
    for gone in ("trade_side", "trade_price", "trade_qty", "sell_price=", "sell_qty="):
        assert gone not in src, gone


# ── U4: quant cockpit ────────────────────────────────────────────────────
QUANT = STATIC / "quant.html"


def _quant_route_paths():
    from fastapi.routing import APIRoute
    from src.api import quant as qapi
    return {r.path for r in qapi.router.routes if isinstance(r, APIRoute)}


def _call_text(src, start):
    """The full `qapi(...)` call starting at `start`, by paren balance."""
    depth, i = 0, src.index("(", start)
    for j in range(i, len(src)):
        if src[j] == "(":
            depth += 1
        elif src[j] == ")":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    return src[start:]


def test_quant_page_calls_only_existing_routes():
    """Every qapi('/x') and '/api/quant/x' literal resolves to a route on the
    quant router. A literal ending in '/' is followed by a path param."""
    src = QUANT.read_text()
    paths = _quant_route_paths()
    lits = set(re.findall(r"qapi\('([^']+)'", src))
    lits |= {p[len("/api/quant"):] for p in re.findall(r"'(/api/quant/[^']+)'", src)}
    bad = []
    for lit in lits:
        lit = lit.split("?")[0]
        ok = (any(p.startswith("/api/quant" + lit + "{") for p in paths)
              if lit.endswith("/") else ("/api/quant" + lit) in paths)
        if not ok:
            bad.append(lit)
    assert not bad, f"quant.html calls routes that do not exist: {sorted(bad)}"


def test_quant_page_reads_the_uiless_apis():
    """/snapshots, /orders, /sync, entry_rationale and the cycle header fields
    had zero readers (ui-01-prd §2.2)."""
    src = QUANT.read_text()
    for needle in ("'/snapshots", "'/orders", "'/sync", "entry_rationale",
                   "S.newsGrid(", "/api/analysis/news/feed",
                   "/api/analysis/summary", "/api/market/ohlcv", "S.drawCurve(",
                   "S.drawChart(", "S.verdictBadge(", "stock-card"):
        assert needle in src, needle
    for field in ("focus", "ended_at", "phase"):
        assert re.search(rf"\.{field}\b", src), f"cycle.{field} is fetched but not shown"


def test_quant_guarded_writes_carry_the_pin():
    """The two-tier rule (ops notes §10): new plan · mode · cancel_all · sync
    re-send the PIN in the body. Every such call site on the page does."""
    src = QUANT.read_text()
    guarded = ("/sync", "/mode", "/orders/cancel_all", "/plans")
    seen, bad = 0, []
    for m in re.finditer(r"qapi\('(" + "|".join(re.escape(g) for g in guarded) + r")(\?[^']*)?'", src):
        call = _call_text(src, m.start())
        if "method: 'POST'" not in call:
            continue
        seen += 1
        if not re.search(r"\bpin\b", call):
            bad.append(call.splitlines()[0])
    assert seen >= 5, "expected the guarded POST call sites (plans×3, mode×2, cancel_all, sync×2)"
    assert not bad, "guarded POST without pin in body:\n" + "\n".join(bad)
    # the force path is a second confirmation, never the first call
    assert "'/sync?force=1'" in src
    # structured refusal, never a string match on the error sentence
    assert "refused === 'full_close'" in src
    assert "none of the ledger" not in src


def test_quant_plan_author_is_a_column():
    src = QUANT.read_text()
    assert "[사용자" not in src, "author detection by note marker is gone"
    assert re.search(r"\.source\s*===\s*'user'", src)


def test_quant_journal_kind_is_not_whitelisted():
    """Unknown kinds render with the slug and a fallback colour (FR-D3), and
    the clamp is measured, not a character count (FR-D4)."""
    src = QUANT.read_text()
    assert re.search(r"KIND_LABEL\[[^\]]+\]\s*\|\|", src)
    assert re.search(r"KIND_COLOR\[[^\]]+\]\s*\|\|", src)
    assert not re.search(r"\.length\s*>\s*\d{2,}", src), "character-count clamp threshold"
    assert "ResizeObserver" in src
    assert "scrollHeight > " in src


def test_quant_page_has_no_stale_sections():
    """The body is two layers; the rest lives behind buttons (FR-C1/C5)."""
    src = QUANT.read_text()
    assert 'id="sec-now"' in src and 'id="sec-positions"' in src
    for pid in ("panel-plans", "panel-watch", "panel-news"):
        assert re.search(rf'id="{pid}"[^>]*\bhidden\b', src), f"{pid} must start collapsed"
    assert 'class="nav"' not in src
