"""SCR-NEWS 수집 (`/`) and SCR-THEME 분야 패널 — the redesigned news page,
checked from source (no browser): it is what `/` serves, the grid has a row
per field and nothing written on a mood cell, fields show by icon only, the
state sentences of the design frames are there in 해요체, and the page keeps
the screen rules of ops notes §11 (tokens, breakpoints, CSP, one label per
action). The pure helpers in news/util.js run in node where node exists."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.dashboard_web import server
from tests.web.test_ui_invariants import BANNED_WORDS, BREAKPOINTS, SHARED_NAMES

STATIC = Path(server.__file__).resolve().parent / "static"
PAGE = STATIC / "news.html"
SCRIPTS = sorted((STATIC / "news").glob("*.js"))
FILES = [PAGE] + SCRIPTS + sorted((STATIC / "news").glob("*.css"))
NEWS_THEMES = Path(server.__file__).resolve().parents[1] / "research" / "news" / "themes.json"
HOST = {"host": "portfolio.jsyoo.dev"}


def _src(p: Path) -> str:
    return p.read_text()


def _code(p: Path) -> str:
    """Source without // and /* */ comments — what a reader can see."""
    s = re.sub(r"/\*.*?\*/", "", _src(p), flags=re.S)
    return "\n".join(line.split("//")[0] if not line.lstrip().startswith("<") else line
                     for line in s.splitlines())


def _all_code() -> str:
    return "\n".join(_code(p) for p in FILES)


# ── route ────────────────────────────────────────────────────────────────
def test_root_serves_the_news_page():
    r = TestClient(server.app).get("/", headers=HOST)
    assert r.status_code == 200
    body = r.text
    assert "<title>portfolio · 수집</title>" in body
    for part in ("news/util.js", "news/theme-panel.js", "news/news.js", "common/accounts-menu.js"):
        assert f'data-include="{part}"' in body, part
    # every inline tag carries the response nonce (CSP)
    nonce = re.search(r"script-src 'nonce-([^']+)'", r.headers["content-security-policy"]).group(1)
    assert body.count("<script") == body.count(f'<script nonce="{nonce}"')
    assert body.count("<style") == body.count(f'<style nonce="{nonce}"')
    assert 'return _serve("news.html")' in Path(server.__file__).read_text().split("def news_page()")[1].split("\n\n")[0]


def test_old_landing_is_gone():
    """The redesign's last step removed the old landing page and its card CSS."""
    assert not (STATIC / "landing.html").exists()
    assert not (STATIC / "legacy.css").exists()


# ── the grid ─────────────────────────────────────────────────────────────
def test_one_row_per_field_and_23_fields():
    themes = json.loads(NEWS_THEMES.read_text())["themes"]
    assert len(themes) == 23
    js = _src(STATIC / "news" / "news.js")
    # rows come from every field the grid route returns, quiet ones folded below
    assert "ST.themes.themes.map(" in js
    assert "소식 없는 분야 " in js
    assert re.search(r"for \(let i = 0; i < 23; i\+\+\)", js), "loading shows 23 skeleton rows"


def test_no_text_on_mood_cells():
    """인계 §3.8: strong-negative fill has too little contrast for text — the
    cell is an empty span with an aria-label, never children."""
    js = _src(STATIC / "news" / "news.js")
    cells = re.findall(r"el\('span', \{ className: 'mc .*?\}\)", js, re.S)
    assert cells, "mood cell builder not found"
    for c in cells:
        assert "'aria-label'" in c
        assert not re.search(r"\}\s*,\s*\[", c), "a mood cell got children"


def test_fields_by_icon_only():
    code = _all_code()
    assert "newsThemeColor" not in code and "theme_colors" not in code
    assert "S.newsThemeIcon(" in code


def test_mood_uses_the_shared_band_and_tokens():
    code = _all_code()
    assert "S.moodBand(" in code
    for band in ("positive-strong", "positive", "neutral", "negative", "negative-strong", "none", "error"):
        assert f".b-{band}" in _src(PAGE), band


# ── words ────────────────────────────────────────────────────────────────
STATE_WORDS = (
    "분야 분위기 7일", "오늘의 핵심", "뉴스 흐름", "소셜", "내 종목",
    "오늘 수집 전이에요",                       # predawn
    "소식을 불러오지 못했어요", "에 모은 소식이에요",   # error (+ stale view)
    "분위기 점수를 불러오지 못했어요", "첫 줄은 그대로 보여요",   # theme_route_error
    " 내 종목 분야 소식이 없어요", "이 기기에서 연 계좌가 없어요",   # mine_none · no account
    "오늘 소식이 없어요",                       # empty_expanded · no_today
    " 이 기기에서 잠갔어요",                    # after-lock
    "이 분야 소식을 불러오지 못했어요", "이 분야에 연결된 내 종목이 없어요",
    "첫 주간 요약은 일요일 저녁에 나와요", "선은 5일째부터 그려요", "점수는 없어요",
)


def test_state_sentences_present():
    code = _all_code()
    missing = [w for w in STATE_WORDS if w not in code]
    assert not missing, missing


def test_haeyo_not_declarative():
    """User-facing strings end in 해요체, never a '~다' statement."""
    bad = []
    for p in FILES:
        for i, line in enumerate(_code(p).splitlines(), 1):
            for s in re.findall(r"'([^'\n]*)'", line):
                if re.search(r"[가-힣]다\.?$", s.strip()) and not s.strip().endswith("없다"):
                    bad.append(f"{p.name}:{i}: {s}")
    assert not bad, bad


def test_banned_words_and_label_dictionary():
    code = _all_code()
    hits = [w for w in BANNED_WORDS if w in code]
    assert not hits, hits
    for key in ("retry", "clearFilter", "showAll", "collapse"):
        assert f"S.LABELS.{key}" in code, key


# ── screen rules (ops notes §11) ────────────────────────────────────────────
_HEX_VALUE = re.compile(r"[:=(,'\"`]\s*#[0-9a-fA-F]{3,8}(?![\w-])")


def test_tokens_only():
    bad = []
    legacy = re.compile(r"var\(--(bg|panel|elevated|grid|line|line-strong|fg|dim|muted|cyan|green|yellow|orange|red|purple)\)")
    for p in FILES:
        for i, line in enumerate(_src(p).splitlines(), 1):
            if (_HEX_VALUE.search(line) or re.search(r"\brgba?\(\s*\d", line) or "--primitive-" in line
                    or legacy.search(line) or re.search(r"font-size:\s*\d", line)):
                bad.append(f"{p.name}:{i}: {line.strip()}")
    assert not bad, bad


def test_breakpoints_canonical_min_width_only():
    css = _src(PAGE)
    for m in re.finditer(r"@media([^{]*)\{", css):
        q = m.group(1)
        for px in re.findall(r"(\d+)px", q):
            assert px in BREAKPOINTS, q
        assert "max-width" not in q, q


def test_csp_clean_markup():
    html = _src(PAGE)
    assert not re.search(r"\sstyle=\"", html), "style attribute"
    assert not re.search(r"\son[a-z]+=", html), "inline handler"
    code = _all_code()
    assert ".innerHTML" not in code and "insertAdjacentHTML" not in code
    assert "setAttribute('style'" not in code
    assert "</script" not in "".join(_src(p) for p in SCRIPTS)


def test_button_family_only():
    css = _src(PAGE)
    assert not re.search(r"\.btn-(?!primary\b|danger\b|sm\b)[a-z]", css)


def test_a11y_floor():
    html = _src(PAGE)
    assert '<a class="skip-link" href="#main">' in html and '<main id="main"' in html
    assert re.search(r"<h1[^>]*>", html)
    code = _all_code()
    assert "'aria-expanded'" in code and "'aria-pressed'" in code   # fold row · 내 종목 · 전체 보기
    assert "S.whenVisible(" in code
    # links out open a new tab without handing it this page
    for m in re.finditer(r"target: '_blank'[^}]*", code):
        assert "noopener" in m.group(0)


def test_shared_helpers_through_S():
    bad = []
    for p in SCRIPTS:
        src = _code(p)
        pulled = set()
        for m in re.finditer(r"const\s*\{([^}]*)\}\s*=\s*S\b", src):
            pulled |= {n.strip() for n in m.group(1).split(",") if n.strip()}
        for name in SHARED_NAMES:
            if name in pulled:
                continue
            assert not re.search(rf"^(?:function\s+{name}\s*\(|(?:const|let|var)\s+{name}\s*=)", src, re.M), \
                f"{p.name} redefines shared {name}"
            for i, line in enumerate(src.splitlines(), 1):
                if re.search(rf"(?<![\w.$]){name}\s*\(", line):
                    bad.append(f"{p.name}:{i}: {line.strip()}")
    assert not bad, bad


def test_icons_in_sprite():
    sprite = set(re.findall(r'<symbol id="i-([a-z0-9-]+)"', _src(STATIC / "shared.js")))
    used = set()
    for p in FILES:
        used |= set(re.findall(r"svgIco\('([a-z0-9-]+)'", _src(p)))
        used |= set(re.findall(r'href="#i-([a-z0-9-]+)"', _src(p)))
    assert used and not (used - sprite), sorted(used - sprite)
    # source names come from the shared map (S.srcName)
    assert "function srcName(" in _src(STATIC / "shared.js")


# ── data boundary (ops notes §4, brief) ─────────────────────────────────────
def test_mine_reads_only_this_devices_books():
    js = _code(STATIC / "news" / "news.js")
    # the accounts come from this device's session, never the full list
    assert "'/api/accounts/session'" in js
    assert re.search(r"openIds\.map\(id => S\.api\('/api/accounts/' \+ encodeURIComponent\(id\)", js)
    # the shared route is handed the symbols (it never reads a book)
    assert "/api/analysis/news/symbol-themes?symbols=" in js
    # a locked book never opens a PIN gate on this page, and amounts never land in storage
    assert "S.onForbidden" not in js
    assert "gate: false" in js
    for m in re.finditer(r"localStorage\.setItem\(([^;]*)\)", js):
        assert "mine" not in m.group(1).lower() and "total" not in m.group(1).lower()


def test_open_routes_only_before_a_session():
    """The page's own reads must work without any cookie (news is public)."""
    open_ = server._OPEN_API
    assert ("GET", "/api/analysis/news/feed") in open_
    assert ("GET", "/api/analysis/news/themes") in open_
    assert ("GET", "/api/analysis/news/themes/") in server._OPEN_PREFIX


# ── helpers, run in node ────────────────────────────────────────────────
NODE = shutil.which("node")

HARNESS = r"""
const vm = require('vm'), fs = require('fs');
const ctx = { window: { __THEME_TOKENS: JSON.parse(process.argv[3]) }, document: { addEventListener() {}, body: null },
              Intl, Date, Math, JSON, Promise, setTimeout, console };
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(process.argv[1], 'utf8'), ctx);
ctx.S = ctx.window.S;
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), ctx);
const U = ctx.window.NEWS_UTIL;
const cases = JSON.parse(process.argv[4]);
process.stdout.write(JSON.stringify(cases.map(([fn, args]) => { try { return U[fn](...args); } catch (e) { return 'THROW ' + e.message; } })));
"""


@pytest.mark.skipif(not NODE, reason="node not installed")
def test_util_values():
    tokens = server._theme_style_tag().split("window.__THEME_TOKENS = ", 1)[1].rsplit(";</script>", 1)[0]
    cases = [
        ("fmtScore", [0.38]), ("fmtScore", [-0.41]), ("fmtScore", [0]), ("fmtScore", [None]),
        ("scoreTone", [0.3]), ("scoreTone", [-0.1]), ("scoreTone", [0.05]), ("scoreTone", [None]),
        ("addDays", ["2026-10-01", -1]), ("addDays", ["2026-12-31", 1]),
        ("itemTs", [{"published_iso": "2026-10-14T16:54:55"}]),
        ("itemTs", [{"published_iso": "2026-10-14T07:00:36+00:00"}]),
        ("itemDay", [{"fetched_at": "2026-10-14T17:00:01.484372+09:00"}]),
        ("itemDay", [{"fetched_at": "2026-10-14T16:00:00+00:00"}]),
        ("shiftText", ["2026-10-12: improving→deteriorating"]), ("shiftText", [""]),
        ("objParticle", ["준ISA"]), ("objParticle", ["준연금"]), ("objParticle", ["해외투자"]),
    ]
    r = subprocess.run([NODE, "-e", HARNESS, str(STATIC / "shared.js"), str(STATIC / "news" / "util.js"),
                        tokens, json.dumps(cases)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    M = "−"
    assert json.loads(r.stdout) == [
        "+0.38", M + "0.41", "0.00", "—",
        "pos", "neg", "neutral", "none",
        "2026-09-30", "2027-01-01",
        "2026-10-14T16:54:55+09:00", "2026-10-14T07:00:36+00:00",
        "2026-10-14", "2026-10-15",
        "마지막 전환 10/12(월) · 개선 → 악화", "",
        "를", "을", "를",
    ]
