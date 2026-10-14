"""sources.html on the token layer, and the invariants of its design: one
table whose number columns switch between today and the last 7 days, the
check list on top in both views, fields shown by icon only, the state
vocabulary and the word '글'. Static checks on the source file."""
import json
import re
from pathlib import Path

from src.dashboard_web import server

STATIC = Path(server.__file__).resolve().parent / "static"
SRC = (STATIC / "sources.html").read_text()
SCRIPT = SRC[SRC.index("<script>"):]


def test_no_font_size_literals():
    bad = [m.group(0) for m in re.finditer(r"font-size:\s*\d+px", SRC)]
    assert not bad, f"font-size literals — use --text-*/--h*: {bad}"


def test_mobile_first_breakpoints():
    queries = re.findall(r"@media[^{]*", SRC)
    assert queries
    for q in queries:
        assert "max-width" not in q, q
        for px in re.findall(r"(\d+)px", q):
            assert px in {"600", "900", "1100"}, q


def test_a11y_skeleton():
    body = SRC[SRC.index("<body>"):]
    assert re.match(r'<body>\s*<a class="skip-link" href="#main">본문으로 건너뛰기</a>', body)
    assert re.search(r'<main\b[^>]*\bid="main"', SRC)
    assert len(re.findall(r"<h1\b", SRC)) == 1
    # the detail panel is the shared S.panel: role=dialog, focus kept inside,
    # Esc closes (the overlay stack in shared.js)
    assert "S.panel(" in SCRIPT
    js = (STATIC / "shared.js").read_text()
    panel = js[js.index("function panel("):js.index("function menu(")]
    assert "role: 'dialog'" in panel and "'aria-modal': 'true'" in panel and "trapFocus(" in panel
    assert "onEscape" in panel
    assert "'Escape'" in SCRIPT, "Esc hides the field-name bubble"


def test_polls_every_five_minutes_while_visible():
    assert "S.whenVisible(load, POLL_MS)" in SCRIPT
    assert re.search(r"POLL_MS\s*=\s*5\s*\*\s*60\s*\*\s*1000", SCRIPT)
    assert "setInterval(" not in SRC


def test_csp_clean_markup():
    assert 'style="' not in SRC, "inline style attributes are blocked by the nonce CSP"
    assert not re.search(r"\son[a-z]+=", SRC), "inline event handler attributes"


def test_button_family_only():
    css = SRC[:SRC.index("</style>")]
    assert not re.search(r"\.btn-(?!sm\b)[a-z]+", css), "no fifth .btn variant"
    for btn in re.findall(r"<button\b[^>]*>", SRC):
        assert 'class="btn' in btn or "data-view" in btn, btn   # .seg buttons are the shared segment


def test_state_vocabulary():
    for word in ("'수집 오류'", "'실패 중'", "'자주 실패'", "'새 글 없음'", "'다음 수집 때 확인'", "'정상'"):
        assert word in SCRIPT, word
    for todo in ("수정 배포 뒤 다음 수집 때 확인", "오류가 이어지면 주소 점검",
                 "다른 주소를 찾거나 소스에서 빼기", "피드 주소가 맞는지 확인", "다음 수집 결과 확인"):
        assert todo in SCRIPT, todo
    assert re.search(r"CHECK_ORDER\s*=\s*\['collect_error',\s*'failing',\s*'quiet',\s*'flaky',\s*'never'\]",
                     SCRIPT)


def test_unit_is_geul_never_gisa():
    assert "기사" not in SRC


def test_one_table_two_views():
    """The view switch changes only the numbers: one header row, one table."""
    assert 'id="view-seg"' in SRC and 'data-view="today"' in SRC and 'data-view="week"' in SRC
    assert "최근 7일" in SRC
    heads = re.findall(r"<thead>.*?</thead>", SRC[SRC.index('id="src-table"'):], re.DOTALL)[0]
    cols = [re.sub(r"<br>", " ", c) for c in re.findall(r"<th scope=\"col\"[^>]*>(.*?)</th>", heads)]
    assert cols == ["소스", "주로 다루는 분야", "상태", "수집 결과", "새 글", "키워드로 거른 글",
                    "중요한 글", "분야 없음", "참고"], cols
    assert SRC.count('id="src-table"') == 1


def test_check_list_sits_on_top_in_both_views():
    content = SRC[SRC.index('id="content"'):]
    assert content.index('id="sec-check"') < content.index('class="side"') < content.index('id="src-table"')
    assert 'grid-template-areas:"check check" "srcs side"' in SRC
    # the view switch re-renders the sources only, never the check list
    switch = SCRIPT[SCRIPT.index("#view-seg button').forEach"):]
    switch = switch[:switch.index("}));")]
    assert "renderSources(DATA)" in switch and "renderCheck" not in switch


def test_fields_are_icons_without_colour():
    assert "newsThemeColor" not in SRC, "fields show by icon only"
    assert "S.newsThemeIcon(" in SRC


def test_no_holding_or_account_names():
    """The report carries counts only; the page must not reach for names."""
    for bad in ("/api/accounts", "symbol_id", "holdings"):
        assert bad not in SCRIPT, bad


def test_source_names():
    js = (STATIC / "shared.js").read_text()
    assert "theelec_en:'디일렉(영문)'" in js
    assert "thelec_battery:'디일렉 배터리'" in js


def test_icons_in_page_sprite():
    """Every icon the script names exists either in the shared sprite or in
    this page's own — a missing <symbol> renders as nothing, silently."""
    shared = (STATIC / "shared.js").read_text()
    used = set(re.findall(r"svgIco\('([a-z0-9-]+)'\)", SCRIPT))
    used |= set(re.findall(r"'([a-z0-9-]+)'", SCRIPT[SCRIPT.index("const SRC_ICON"):SCRIPT.index("const CHECK_ORDER")]))
    used |= set(re.findall(r'href="#i-([a-z0-9-]+)"', SRC))
    used -= {"collect_error", "failing", "flaky", "quiet", "never", "ok"}
    missing = sorted(i for i in used if f'id="i-{i}"' not in SRC and f'id="i-{i}"' not in shared)
    assert not missing, missing


def test_wears_the_redesign_chrome():
    """Redesign stage 1 pilot: top bar (수집 area, 390 title + 새로고침),
    bottom tab, big segment on phones, white cards, no page-local sprite."""
    assert "S.chrome(" in SCRIPT and "active: 'news'" in SCRIPT and "title: '소스 현황'" in SCRIPT
    assert "bottomTab: 'news'" in SCRIPT
    assert 'class="seg seg-lg view-seg"' in SRC
    assert 'class="card' in SRC and 'class="panel' not in SRC
    assert "<symbol" not in SRC, "icons come from the shared sprite"
    assert "JetBrains" not in SRC and "@import" not in SRC, "Pretendard from shared.css"
    for old in ("var(--fg)", "var(--dim)", "var(--cyan)", "var(--panel)", "var(--space-1)", "var(--text-xs)"):
        assert old not in SRC, f"legacy alias {old} — use the direction-A tokens"
