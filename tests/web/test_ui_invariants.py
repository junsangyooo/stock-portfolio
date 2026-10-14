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
SCREEN_FILES = ("landing.html", "account.html", "sources.html", "shared.js", "shared.css", "legacy.css")

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
    # redesign stage 1 (FOUNDATION.md): formatters and rules of 인계 §3.1–3.5
    "fmtPrice", "fmtMoney", "fmtPnl", "fmtPnlPct", "fmtChange", "fmtChangePct", "fmtQty",
    "fmtAxisMan", "fmtDate", "fmtDateTime", "pnlLabel", "quoteState", "analysisState", "moodBand",
    # …and the shared parts
    "topBar", "bottomTab", "chrome", "segmented", "dialog", "panel", "menu", "pinGate",
    "pinInput", "pinPrompt", "stateBlock", "banner", "skeleton", "spinner",
)

LEGACY_PAGES = ("landing.html", "account.html")   # redone in redesign stage 3
STAGE1_NAMES = set(SHARED_NAMES[SHARED_NAMES.index("fmtPrice"):])

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
    that can drift. Drift is allowed to exist but not to be silent. Fields
    show by icon only (user decision): no colour on a theme token; the old
    colours live in legacy.theme_colors until landing/account are redone."""
    import json
    ids = [t["theme_id"] for t in json.loads(NEWS_THEMES.read_text())["themes"]]
    theme = json.loads(THEME.read_text())
    tokens = theme["themes"]
    missing = [i for i in ids if i not in tokens]
    assert not missing, f"news themes without an icon token: {missing}"
    for tid in ids:
        assert set(tokens[tid]) == {"icon"}, tid
        assert re.fullmatch(r"[a-z0-9-]+", tokens[tid]["icon"]), tid
    assert set(theme["legacy"]["theme_colors"]) <= set(ids)


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
                for f in SCREEN_FILES if f.endswith((".js", ".html"))
                # account.html keeps page-local fmtPrice/fmtQty until stage 3
                # replaces it; it never reads the shared ones bare
                if not (f in LEGACY_PAGES and name in STAGE1_NAMES)}
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
    body = re.sub(r"//[^\n]*", "", exported.group(1))
    names = {n.strip() for n in body.split(",") if n.strip()}
    assert set(SHARED_NAMES) <= names, set(SHARED_NAMES) - names


def test_shared_fragments_injected_in_order(client):
    """<head>: theme tokens → shared.css → shared.js → the page's own
    <style>/<script>. CSS coming later is cascade intent; JS coming later is
    the reason the fragment lives under one namespace (S.*)."""
    for path, host in (("/", PORTFOLIO_HOST), ("/a/x", PORTFOLIO_HOST)):
        body = client.get(path, headers={"host": host}).text
        marks = ['<meta charset', 'id="theme-vars"', "window.__THEME_TOKENS",
                 'id="shared-css"', 'id="shared-js"', "@import url(" if "@import url(" in body else "</head>"]
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
    (tmp_path / "news.html").write_text("<html><head><meta charset=utf-8></head></html>")
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


def _account_src() -> str:
    """account.html as served: the page plus every <!--#include--> piece
    (account/*.js · common/accounts-menu.js), since redesign stage 2 split it."""
    return server.expand_includes(ACCOUNT.read_text())


def test_account_page_reads_current_engine_schema():
    """Keys the 종합 renderer reads must exist in what engine.normalize and
    engine._facts_only actually emit. ANALYSIS_BODY_KEYS in account.html is
    the one list; this test resolves every dotted path against both shapes."""
    from src.research.analyze import engine
    src = _account_src()
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


# ── U5: harness closure ──────────────────────────────────────────────────
PAGES = ("landing.html", "account.html", "sources.html")


def test_pages_route_403_to_their_own_pin_gate():
    src = _account_src()
    m = re.search(r"S\.onForbidden\s*=\s*\(\)\s*=>\s*\{([^}]*)\}", src)
    assert m and "showGate" in m.group(1), "account page: S.onForbidden opens its own PIN gate"
    assert "S.pinGate(" in src
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
    goes through S.api (toast + throw, 403 → PIN gate) — no bare fetch() in
    the page or its pieces; the PIN POSTs live in shared.js (authPin)."""
    src = _account_src()
    bad = [l.strip() for l in src.splitlines() if re.search(r"(?<![\w.])fetch\(", l)]
    assert not bad, "bare fetch() on the account page:\n" + "\n".join(bad)
    assert src.count("S.api(") >= 10
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
    # the account page's gate is the shared one, whose six cells carry labels
    assert "S.pinGate(" in _account_src()
    pin = js[js.index("function pinInput"):]
    assert "'aria-label'" in pin[:pin.index("\n}\n")], "PIN cells need an aria-label"


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


def _theme():
    import json
    return json.loads(THEME.read_text())


# Text colours and the surfaces they sit on (Figma: white cards on the subtle
# page). WCAG AA 4.5:1 for every pair; text/disabled is exempt (WCAG 1.4.3
# excludes inactive controls) and fills (mood cells, verdict/negative) carry
# no text.
TEXT_TOKENS = ("text/primary", "text/muted", "accent/default", "accent/hover",
               "status/success", "status/warning", "status/error", "status/error-hover",
               "market/up", "market/down", "verdict/positive", "verdict/negative-text",
               "mood/negative-text")
TEXT_SURFACES = ("bg/default", "bg/subtle")
INVERSE_PAIRS = (("text/inverse", "bg/inverse"), ("text/inverse", "accent/default"),
                 ("text/inverse", "accent/hover"), ("text/inverse", "status/error"),
                 ("text/inverse", "status/error-hover"), ("text/inverse", "verdict/negative-fill"),
                 ("text/inverse", "mood/positive-strong"))
LEGACY_TEXT = ("fg", "dim", "muted", "cyan", "green", "red", "yellow", "orange", "purple")


def test_theme_text_tokens_meet_contrast():
    values = server.theme_values(_theme())
    legacy = _theme()["legacy"]
    bad = []
    for tok in TEXT_TOKENS:
        for surface in TEXT_SURFACES:
            r = _contrast(values[tok], values[surface])
            if r < 4.5:
                bad.append(f"{tok} on {surface}: {r:.2f}")
    for fg, bg in INVERSE_PAIRS:
        r = _contrast(values[fg], values[bg])
        if r < 4.5:
            bad.append(f"{fg} on {bg}: {r:.2f}")
    # the temporary Ink wash aliases keep the old pages readable too
    old = {k: values[v] for k, v in legacy["colors"].items()}
    for tok in LEGACY_TEXT:
        for surface in ("bg", "panel"):
            r = _contrast(old[tok], old[surface])
            if r < 4.5:
                bad.append(f"legacy {tok} on {surface}: {r:.2f}")
    for tid, col in legacy["theme_colors"].items():
        r = _contrast(col, old["panel"])
        if r < 3.0:
            bad.append(f"legacy theme {tid} on panel: {r:.2f}")
    assert not bad, "contrast below AA:\n" + "\n".join(bad)


def test_theme_is_the_design_file_token_set():
    """tokens.items of the design project (156, primitive + semantic) moved
    over unrenamed; code-only additions sit apart in code_tokens."""
    theme = _theme()
    toks = theme["tokens"]
    assert len(toks) == 156
    assert {t["layer"] for t in toks} == {"primitive", "semantic"}
    names = [t["name"] for t in toks + theme["code_tokens"]]
    assert len(names) == len(set(names))
    by = {t["name"]: t for t in toks}
    for t in toks:
        if "alias" in t:
            assert by[t["alias"]]["layer"] == "primitive", t["name"]
    for name in ("bg/default", "bg/subtle", "text/primary", "accent/default", "market/up",
                 "market/down", "mood/positive-strong", "verdict/negative-text", "skeleton/fill",
                 "space/page", "radius/card", "type/body/size", "type/number-lg/style"):
        assert name in by, name
    assert all(t["layer"] == "code" and t.get("source") for t in theme["code_tokens"])
    assert "다크 모드 값은 없다" in theme["description"]


def test_css_variable_naming_rule():
    """One deterministic rule: 'a/b/c' → --a-b-c. No two tokens (or a token
    and a legacy alias) may land on the same variable."""
    theme = _theme()
    assert "'/'를 '-'로" in theme["description"]
    assert server.css_var("bg/default") == "--bg-default"
    assert server.css_var("type/heading-1/size") == "--type-heading-1-size"
    names = [t["name"] for t in theme["tokens"] + theme["code_tokens"]]
    vars_ = [server.css_var(n) for n in names]
    legacy = ["--" + k for k in list(theme["legacy"]["colors"]) + list(theme["legacy"]["tokens"])]
    legacy += [v + "-rgb" for v in legacy]
    assert len(set(vars_ + legacy)) == len(vars_) + len(legacy)
    tag = server._theme_style_tag()
    assert "--bg-default: var(--primitive-neutral-0);" in tag
    assert "--primitive-neutral-0: #FFFFFF;" in tag
    assert "--type-body-size: var(--primitive-size-14);" in tag
    assert "--primitive-size-14: 14px;" in tag
    assert "--primitive-weight-number: 600;" in tag
    assert "--type-body-line-height: 1.5;" in tag
    assert "Pretendard Variable" in tag and "--primitive-icon-set" not in tag
    # legacy aliases point at the new tokens (3단계 끝에 지움)
    assert "--cyan: var(--accent-default);" in tag and "--cyan-rgb: 11, 122, 107;" in tag
    assert "3단계" in theme["legacy"]["note"]


def test_pages_use_semantic_tokens_only():
    """Primitives exist so semantic tokens can point at them; a page that
    reaches past the semantic layer breaks the next palette change."""
    bad = []
    for f in SCREEN_FILES:
        for i, line in enumerate((STATIC / f).read_text().splitlines(), 1):
            if "--primitive-" in line or re.search(r"designColor\('primitive/", line):
                bad.append(f"{f}:{i}: {line.strip()}")
    assert not bad, "primitive tokens used directly:\n" + "\n".join(bad)


def test_theme_has_layout_tokens():
    values = server.theme_values(_theme())
    for name in ("space/gap-xs", "space/gap-sm", "space/gap-md", "space/gap-lg", "space/card",
                 "space/section", "space/page", "space/page-compact",
                 "type/caption/size", "type/body/size", "type/heading-2/size", "type/heading-1/size",
                 "type/body/line-height", "bp/sm", "bp/md", "bp/lg",
                 "layout/topbar-height", "layout/topbar-height-compact", "layout/bottomtab-height",
                 "layout/content-max", "layout/panel-min", "layout/panel-max", "control/height-seg-lg"):
        assert name in values, name
    assert float(values["type/body/line-height"]) >= 1.5
    assert (values["bp/sm"], values["bp/md"], values["bp/lg"]) == ("600px", "900px", "1100px")
    assert values["control/height-seg-lg"] == "49px"
    assert "--space-gap-xs" in server._theme_style_tag()
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
    assert re.search(r"body\s*\{[^}]*line-height:\s*var\(--type-body-line-height\)", css)
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
    for word in ("삭제", "취소", "확인", "저장", "닫기", "다시 분석", "전체 보기", "접기",
                 # 인계 §3.6 additions
                 "거르기 풀기", "다시 시도", "버리기", "계속 입력", "되돌리기", "거래 보기", "거래 기록",
                 "매수", "매도", "관심 추가", "보유 정정", "예수금 정정", "이 기기에서 잠그기", "바꾸기",
                 "전량", "현재가", "새 대화", "보내기", "만들기", "계좌 추가", "기록", "이 종목 묻기",
                 "지난 좌표 보기", "날짜 선택"):
        assert f"'{word}'" in m.group(1), word
    for banned in BANNED_WORDS:
        assert banned not in m.group(1)
    assert re.search(r"window\.S\s*=\s*\{[^}]*\bLABELS\b", js)


BANNED_WORDS = ("제거", "해제", "지우기")   # 인계 §3.6 — 삭제 · 거르기 풀기 · 버리기 instead


def test_label_dictionary_used():
    """One verb per action: 삭제, never 제거 — and no 해제 or 지우기 either —
    in anything a user reads (button text, confirmLabel, aria-label, dialog
    body). Comments are exempt."""
    bad = []
    for f in PAGES + ("shared.js",):
        for i, line in enumerate((STATIC / f).read_text().splitlines(), 1):
            code = line.split("//")[0]
            if any(w in code for w in BANNED_WORDS):
                bad.append(f"{f}:{i}: {line.strip()}")
    assert not bad, "banned words in user-facing text — use S.LABELS:\n" + "\n".join(bad)


# Every Lucide icon the design file uses (Figma 디자인 page, Icon/* instances,
# read 2026-10-14) — the 23 field icons and the six source-status icons included.
FIGMA_ICONS = (
    "activity", "arrow-left", "arrow-left-right", "arrow-up-down", "arrow-up-to-line", "atom",
    "ban", "battery-charging", "bot", "brain-circuit", "briefcase", "car", "chart-line",
    "chevron-down", "chevron-right", "circle-check", "circle-dashed", "circle-x", "cpu",
    "download", "ellipsis", "external-link", "factory", "flame", "gamepad-2", "gem", "globe",
    "heart-pulse", "info", "landmark", "loader-circle", "lock", "message-circle", "moon",
    "newspaper", "pencil", "pill", "plus", "printer", "refresh-cw", "rocket", "search",
    "server", "shield", "ship", "shopping-cart", "star", "trending-down", "triangle-alert",
    "wallet", "wrench", "x", "zap")


def test_sprite_has_every_design_icon():
    js = SHARED_JS.read_text()
    sprite = set(re.findall(r'<symbol id="i-([a-z0-9-]+)"', js))
    missing = sorted(set(FIGMA_ICONS) - sprite)
    assert not missing, f"Figma icons absent from the sprite: {missing}"
    # every literal icon name the shared parts and the redesigned pages use
    used = set(re.findall(r"svgIco\('([a-z0-9-]+)'", js))
    used |= set(re.findall(r"icon: '([a-z0-9-]+)'", js))
    for f in ("sources.html",):
        src = (STATIC / f).read_text()
        used |= set(re.findall(r"svgIco\('([a-z0-9-]+)'", src))
        used |= set(re.findall(r'href="#i-([a-z0-9-]+)"', src))
    assert not sorted(used - sprite), sorted(used - sprite)
    assert "Lucide 1.55.0" in js
