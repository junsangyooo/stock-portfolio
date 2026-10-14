"""Screen invariants, enforced from source: the pages never point at the
quant product, the shared floor (theme tokens, shared.css, shared.js) is
injected the same way into every page, and the rules of ops notes §11 화면
hold in every file.
"""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.dashboard_web import server

STATIC = Path(server.__file__).resolve().parent / "static"
PORTFOLIO_HOST = "portfolio.jsyoo.dev"


@pytest.fixture
def client():
    return TestClient(server.app)


def _hits(name: str, patterns: list[str]) -> list[str]:
    lines = (STATIC / name).read_text().splitlines()
    return [f"{name}:{i}: {line.strip()}"
            for i, line in enumerate(lines, 1)
            if any(re.search(p, line, re.IGNORECASE) for p in patterns)]


def test_no_crosslinks_to_quant():
    """Case-insensitive on purpose: with case respected a wordmark like
    '◆ QUANT-LAB' on the portfolio screen passes untouched."""
    to_quant = [r"/q\b", r"quant", r"pdash_q"]        # 'quant' covers its API paths too
    bad = _hits("landing.html", to_quant) + _hits("account.html", to_quant)
    assert not bad, "references to the quant product:\n" + "\n".join(bad)


# ── U2: shared floor ─────────────────────────────────────────────────────
SHARED_JS = STATIC / "shared.js"
SHARED_CSS = STATIC / "shared.css"
THEME = STATIC / "theme.json"
NEWS_THEMES = Path(server.__file__).resolve().parents[1] / "research" / "news" / "themes.json"
SCREEN_FILES = ("landing.html", "account.html", "sources.html", "shared.js", "shared.css")

# Every name the injected fragment exports (ui-02-architecture §4). Each must
# be defined exactly once across the fragment and the pages.
SHARED_NAMES = (
    "el", "clear", "svgIco", "kstClock",
    "designColor", "designRgba", "newsThemeColor", "newsThemeIcon",
    "srcName", "newsCard", "newsGrid", "toast", "api",
    # U4: the card grammar
    "drawChart", "fmtBarDate", "drawCurve", "LABEL_KO", "freshInfo", "verdictBadge",
    # U5: the one confirm dialog (native confirm() is banned below)
    "confirmModal",
    # modal focus trap and tab-aware polling, used by every page's modals/timers
    "trapFocus", "whenVisible",
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
    for path, host in (("/", PORTFOLIO_HOST), ("/a/x", PORTFOLIO_HOST)):
        body = client.get(path, headers={"host": host}).text
        marks = ['<meta charset', 'id="theme-vars"', "window.__THEME_TOKENS",
                 'id="shared-css"', 'id="shared-js"', "@import url("]
        pos = [body.find(m) for m in marks]
        assert all(p >= 0 for p in pos), (path, dict(zip(marks, pos)))
        assert pos == sorted(pos), (path, dict(zip(marks, pos)))
    src = Path(server.__file__).read_text()
    assert "app.mount(" not in src, "no /static mount — fragments are injected"


def test_missing_fragment_is_a_500_not_a_blank_page(client, monkeypatch, tmp_path):
    """Every page's first line is `const { el, … } = S`, so a page served
    without shared.js renders nothing. That must fail where the deploy health
    check (which now also fetches /) can see it, not as a 200."""
    monkeypatch.setattr(server, "STATIC", tmp_path)          # no fragments here
    (tmp_path / "landing.html").write_text("<html><head><meta charset=utf-8></head></html>")
    r = client.get("/", headers={"host": PORTFOLIO_HOST})
    assert r.status_code == 500 and "shared.css" in r.text


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
    button in an empty/error state re-created the double-run bug.
    """
    src = ACCOUNT.read_text()
    assert "runChartAnalysis" not in src
    assert "watchChartJob" not in src
    assert len(re.findall(r"API\.runAnalysis\(", src)) == 1
    starts = re.findall(r"[\w/]*/run'", src)
    assert len(starts) == 1, starts
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
    """One request per trade, and no follow-up DELETE / POST /holdings (the
    second call used to wipe the memo). A full sell deletes the row on the
    server (C1) — the page reads holding.removed, it never sends a flag."""
    src = ACCOUNT.read_text()
    assert "/trades'" in src
    for gone in ("trade_side", "trade_price", "trade_qty", "sell_price=", "sell_qty=",
                 "remove_when_flat"):
        assert gone not in src, gone
    body = src[src.index("async function submit() {\n    if (!currentStock) return;"):]
    body = body[:body.index("// edit — a direct correction")]
    assert "d.holding.removed" in body
    assert "plan_id" not in body
    for gone in ("API.removeHolding(", "API.addHolding("):
        assert gone not in body, gone


# ── U5: harness closure ──────────────────────────────────────────────────
PAGES = ("landing.html", "account.html", "sources.html")


def test_facts_only_result_is_not_dressed_as_a_judgement():
    """engine._facts_only emits confidence 0.0 — a number, so `!= null` alone
    would draw 「신뢰도 ○○○○○」 under a result that holds no judgement."""
    src = ACCOUNT.read_text()
    assert re.search(r"body\.confidence\s*!=\s*null\s*&&\s*!factsOnly", src)


def test_pages_route_403_to_their_own_pin_gate():
    src = (STATIC / "account.html").read_text()
    assert re.search(r"S\.onForbidden\s*=\s*showPinGate\b", src)
    assert "onForbidden" in SHARED_JS.read_text()


def test_no_native_confirm():
    """No native dialogs: confirm() → S.confirmModal, alert() → S.toast.
    (A blocking dialog also freezes browser automation.)"""
    bad = []
    for f in PAGES:
        for i, line in enumerate((STATIC / f).read_text().splitlines(), 1):
            if re.search(r"(?<![\w.])(confirm|alert)\(", line):
                bad.append(f"{f}:{i}: {line.strip()}")
    assert not bad, "native dialog — use S.confirmModal / S.toast:\n" + "\n".join(bad)
    js = SHARED_JS.read_text()
    for needle in ("role: 'dialog'", "'Escape'", "cancel.focus()"):
        assert needle in js, needle


def test_el_attribute_objects_use_the_canonical_keys():
    """class: / onclick: still work (el() maps them) but two spellings of one
    key is how the pages drifted apart before U2."""
    bad = []
    for f in PAGES:
        for i, line in enumerate((STATIC / f).read_text().splitlines(), 1):
            if re.search(r"(?<![\w-])(class|onclick)\s*:", line):
                bad.append(f"{f}:{i}: {line.strip()}")
    assert not bad, "use className: / onClick: in el() props:\n" + "\n".join(bad)


def test_every_page_has_a_favicon(client):
    """The icon is injected by server.py (PNG routes, B8) — the served HTML
    carries it; the page files carry no icon of their own."""
    for path, host in (("/", PORTFOLIO_HOST), ("/a/x", PORTFOLIO_HOST)):
        body = client.get(path, headers={"host": host}).text
        assert '<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32.png">' in body, path
        assert "data:image/svg+xml" not in body, path


def test_ohlcv_and_analysis_fetches_fail_loudly():
    """A swallowed fetch failure renders as 'no data'. Every account-page read
    goes through S.api (toast + throw, 403 → PIN gate); the only bare fetches
    left are the PIN/auth POSTs, the summary loader (handles 403 itself) and
    the analysis job poller."""
    src = ACCOUNT.read_text()
    for call in ("API.ohlcv(", "API.quotes(need", "API.analysisSummary(",
                 "API.analysisStatus(", "API.snapshots(", "API.reorderHoldings(",
                 "API.marketRefresh(", "API.analysisResult(",
                 "API.chartAnalysis(", "API.newsSignals(", "API.fundamentalFacts(",
                 "API.balances(", "API.listAccounts(", "API.search("):
        assert re.search(r"S\.api\(\s*" + re.escape(call), src), call
        assert not re.search(r"\bfetch\(\s*" + re.escape(call), src), "bare " + call
    js = SHARED_JS.read_text()
    assert "네트워크 오류" in js, "S.api toasts a network failure, not only a non-2xx"


def test_403_gate_only_on_pin_required():
    """Only a session-missing 403 ('PIN required') opens the PIN gate; a wrong
    step-up PIN or 'PIN을 먼저 설정하세요' is a real answer and must reach the
    caller inline."""
    js = SHARED_JS.read_text()
    m = re.search(r"async function api\(.*?\n\}", js, re.DOTALL)
    assert m, "shared.js defines api()"
    body = m.group(0)
    assert body.index("'PIN required'") < body.index("onForbidden()"), \
        "api() checks detail === 'PIN required' before calling onForbidden"


def test_a11y_floor():
    css = SHARED_CSS.read_text()
    assert ":focus-visible" in css
    js = SHARED_JS.read_text()
    assert re.search(r"id: 'toast-host'[^}]*role: 'status'[^}]*'aria-live': 'polite'", js)
    src = ACCOUNT.read_text()
    gate = src[src.index("function showPinGate"):]
    gate = gate[:gate.index("\n}\n")]
    assert "'aria-label'" in gate, "account.html: PIN gate input needs an aria-label"


# ── U6: design-token layer (ui-open-decisions B1/B2/B3/B7/B9) ────────────
def _lum(hex_str: str) -> float:
    h = hex_str.lstrip("#")
    def ch(c):
        c = int(c, 16) / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(h[0:2]) + 0.7152 * ch(h[2:4]) + 0.0722 * ch(h[4:6])


def _contrast(a: str, b: str) -> float:
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_theme_text_tokens_meet_contrast():
    """WCAG AA for the 9–12px mono text these tokens colour: 4.5:1 against
    both surfaces text sits on. News-theme colours are badge text → 3:1."""
    import json
    theme = json.loads(THEME.read_text())
    c = theme["colors"]
    bad = []
    for tok in ("fg", "dim", "muted", "cyan", "green", "red", "yellow", "orange", "purple"):
        for surface in ("bg", "panel"):
            r = _contrast(c[tok], c[surface])
            if r < 4.5:
                bad.append(f"{tok} on {surface}: {r:.2f}")
    for tid, v in theme["themes"].items():
        r = _contrast(v["color"], c["panel"])
        if r < 3.0:
            bad.append(f"theme {tid} on panel: {r:.2f}")
    assert not bad, "contrast below AA:\n" + "\n".join(bad)


def test_theme_has_layout_tokens():
    import json
    tokens = json.loads(THEME.read_text())["tokens"]
    for name in ("space-1", "space-2", "space-3", "space-4", "space-5", "space-6",
                 "text-xs", "text-sm", "text-md", "text-lg", "text-xl",
                 "lh-tight", "lh-body", "h1", "h2", "h3", "bp-sm", "bp-md", "bp-lg"):
        assert name in tokens, name
    assert float(tokens["lh-body"]) >= 1.5
    # server.py emits every key under `tokens` as --name; these must reach CSS
    assert "--space-1" in server._theme_style_tag()
    assert "--bp-sm" in server._theme_style_tag()


BREAKPOINTS = {"600", "900", "1100"}   # theme.json bp-sm / bp-md / bp-lg


def test_breakpoints_are_canonical():
    """var() cannot appear inside @media, so the three bp-* values in
    theme.json are repeated as literals — and only those three."""
    bad = []
    for f in PAGES + ("shared.css",):
        for i, line in enumerate((STATIC / f).read_text().splitlines(), 1):
            for m in re.finditer(r"@media[^{]*?(\d+)px", line):
                if m.group(1) not in BREAKPOINTS:
                    bad.append(f"{f}:{i}: {line.strip()}")
    assert not bad, "non-canonical breakpoints:\n" + "\n".join(bad)


def test_shared_css_breakpoints_are_canonical():
    """The shared floor is done already; the page files are the xfail above."""
    for i, line in enumerate(SHARED_CSS.read_text().splitlines(), 1):
        for m in re.finditer(r"@media[^{]*?(\d+)px", line):
            assert m.group(1) in BREAKPOINTS, f"shared.css:{i}: {line.strip()}"


def test_btn_family_and_a11y_base():
    css = SHARED_CSS.read_text()
    for sel in (".btn {", ".btn-primary", ".btn-danger", ".btn-sm",
                ".btn:hover", ".btn:focus-visible", ".btn:disabled",
                "prefers-reduced-motion", "pointer: coarse",
                ".skip-link", ".sr-only", "img {"):
        assert sel in css, sel
    assert "min-height: 24px" in css and "min-height: 44px" in css
    assert re.search(r"body\s*\{[^}]*line-height:\s*var\(--lh-body\)", css)
    # the other shared verbs: no stray 5th variant
    variants = set(re.findall(r"\.btn-([a-z]+)", css))
    assert variants == {"primary", "danger", "sm"}, variants


def test_el_style_via_csstext():
    """setAttribute('style', …) is blocked under a nonce CSP (B4);
    element.style.cssText is not."""
    js = SHARED_JS.read_text()
    assert "e.style.cssText = val" in js
    assert "setAttribute('style'" not in js


def test_label_dictionary_exported():
    js = SHARED_JS.read_text()
    m = re.search(r"const LABELS\s*=\s*\{([^}]*)\}", js)
    assert m, "shared.js defines LABELS"
    for word in ("삭제", "취소", "확인", "저장", "닫기", "다시 분석", "전체 보기", "접기"):
        assert f"'{word}'" in m.group(1), word
    assert "제거" not in m.group(1)
    assert re.search(r"window\.S\s*=\s*\{[^}]*\bLABELS\b", js)


def test_label_dictionary_used():
    """One verb per action: 삭제, never 제거, in anything a user reads
    (button text, confirmLabel, aria-label, dialog body). Comments are exempt."""
    bad = []
    for f in PAGES:
        for i, line in enumerate((STATIC / f).read_text().splitlines(), 1):
            code = line.split("//")[0]
            if "제거" in code:
                bad.append(f"{f}:{i}: {line.strip()}")
    assert not bad, "'제거' in user-facing text — use S.LABELS.delete:\n" + "\n".join(bad)
