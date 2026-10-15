"""account.html (redesign stage 2) invariants, read from source like
test_ui_invariants.py. The page is a shell; its behaviour lives in the
<!--#include--> pieces under static/account/ and static/common/. These
tests hold the account-side contract (core.js · view-account · dialogs ·
settings · chat · accounts menu); the stock pieces have test_ui_stock.py."""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.dashboard_web import server

STATIC = Path(server.__file__).resolve().parent / "static"
SHELL = STATIC / "account.html"
MINE = ("account/core.js", "account/view-account.js", "account/dialogs.js", "account/settings.js",
        "account/chat.js", "common/accounts-menu.js")
MINE_CSS = ("account/account.css", "common/accounts-menu.css")
HOST = {"host": "portfolio.jsyoo.dev"}


def _src(rel):
    return (STATIC / rel).read_text()


def _all():
    return {rel: _src(rel) for rel in MINE + MINE_CSS}


@pytest.fixture
def client():
    return TestClient(server.app)


# ── shell ────────────────────────────────────────────────────────────────
def test_shell_includes_core_before_the_views():
    """core.js makes window.ACC; every view registers on it, so it comes first."""
    html = SHELL.read_text()
    inc = re.findall(r"<!--#include\s+([^\s>]+?)\s*-->", html)
    js = [i for i in inc if i.endswith(".js")]
    assert js[0] == "account/core.js", js
    for need in MINE + ("account/stock.js", "account/trades.js", "account/trade-dialog.js", "account/hold-fix.js"):
        assert need in inc, need
    assert "legacy.css" not in html and "fonts.googleapis" not in html
    assert '<main id="main" class="page"' in html and 'class="skip-link" href="#main"' in html


def test_served_page_inlines_every_piece_with_a_nonce(client):
    r = client.get("/a/x", headers=HOST)
    assert r.status_code == 200
    nonce = re.search(r"'nonce-([^']+)'", r.headers["content-security-policy"]).group(1)
    for rel in MINE:
        m = re.search(r'<script nonce="([^"]+)" data-include="' + re.escape(rel) + '">', r.text)
        assert m and m.group(1) == nonce, rel
    assert "<!--#include" not in r.text


# ── CSP · tokens · breakpoints · words ──────────────────────────────────
def test_no_style_or_handler_attributes_and_no_innerhtml():
    for rel, src in _all().items():
        if rel.endswith(".css"):
            continue
        assert "innerHTML" not in src, rel
        assert not re.search(r"setAttribute\(\s*['\"](style|on\w+)['\"]", src), rel
        assert not re.search(r"(?<![\w-])(class|onclick)\s*:", src), rel + ": use className: / onClick:"
        assert not re.search(r"(?<![\w.])(confirm|alert)\(", src), rel + ": native dialog"
        assert not re.search(r"(?<![\w.])fetch\(", src), rel + ": go through S.api"


def test_no_color_or_font_size_literals():
    for rel, src in _all().items():
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b(?![\w-])", re.sub(r"['\"]#[\w-]+['\"]", "", src)
                             .replace('href="#main"', "")), rel + ": colour literal"
        assert not re.search(r"\brgba?\(\s*\d", src), rel + ": colour literal"
        assert not re.search(r"font-size:\s*\d", src), rel + ": font-size literal"
        assert "--primitive-" not in src, rel + ": semantic tokens only"


def test_breakpoints_are_canonical_min_width():
    for rel in MINE_CSS:
        src = _src(rel)
        assert "max-width" not in re.sub(r"(?<!@media )max-width", "", src.replace("@media not all and (min-width", "")), rel
        for bp in re.findall(r"@media[^{]*?min-width:\s*(\d+)px", src):
            assert bp in {"600", "900", "1100"}, (rel, bp)
    js = _src("account/view-account.js") + _src("common/accounts-menu.js")
    for bp in re.findall(r"min-width:\s*(\d+)px", js):
        assert bp == "600", bp


def test_buttons_are_the_btn_family():
    for rel in MINE:
        for i, line in enumerate(_src(rel).splitlines(), 1):
            for m in re.finditer(r"el\('button',\s*\{[^}]*?className:\s*'([^']*)'", line):
                assert "btn" in m.group(1).split(), f"{rel}:{i}: {line.strip()}"


def test_label_words():
    """'제거' · '해제' · '지우기' are not product words (인계 §3.6)."""
    for rel in MINE:
        src = _src(rel)
        for w in ("제거", "해제", "지우기"):
            assert w not in src, (rel, w)
        # no '~다' declaratives in UI copy: a Korean string ending in 다 / 니다
        for s in re.findall(r"'([^'\n]*[가-힣][^'\n]*)'", src):
            assert not re.search(r"(니다|었다|한다|된다)[.!]?$", s), (rel, s)


# ── contract (brief-common 계좌 화면 계약) ──────────────────────────────
def test_core_defines_the_contract():
    src = _src("account/core.js")
    for name in ("ACC.on =", "ACC.go =", "ACC.params =", "ACC.reload =", "ACC.setChrome =",
                 "views: {}", "panels: {}", "ACC.lock =", "ACC.warm =", "ACC.refreshQuotes ="):
        assert name in src, name
    assert "addEventListener('popstate'" in src and "history.pushState" in src
    assert "ACC.views.account =" in _src("account/view-account.js")
    d = _src("account/dialogs.js")
    for name in ("ACC.openAdd =", "ACC.openCash =", "ACC.confirm ="):
        assert name in d, name
    assert "ACC.chat = {" in _src("account/chat.js")
    assert "ACC.panels.settings =" in _src("account/settings.js")


def test_quotes_warm_on_open_and_every_five_minutes_while_visible():
    src = _src("account/core.js")
    assert "/api/market/quotes?symbols=" in src and "max_age_s=" in src
    assert "WARM_MAX_AGE = 300" in src and "POLL_MS = 5 * 60 * 1000" in src
    assert "S.whenVisible(" in src and "marketInfo(r).phase === 'open'" in src
    # quote state comes from the account's quote_ts, not the /quotes reply
    assert "S.quoteState({ quoteTs: h.quote_ts" in src


def test_market_phase_and_session_date_come_from_the_server_calendar():
    """감사: a weekday holiday read as '장중' all day, and one held symbol's
    newest bar named the P&L day. Both now come from GET /api/market/session,
    read again with every account read; the clock is only the fallback."""
    src = _src("account/core.js")
    assert "'/api/market/session'" in src
    assert "const sess = loadMarketSession();" in src and "await sess;" in src   # with every reload
    assert "S.marketState(region)" in src                                     # fallback only
    assert "/api/market/ohlcv/" not in src and "marketPhase" not in src
    assert "S.pnlLabel(m)" in src


def test_refresh_fills_bars_then_reads_the_account_again():
    src = _src("account/core.js")
    body = src[src.index("ACC.refreshQuotes = async function"):]
    body = body[:body.index("\n  };")]
    assert "S.api('/api/market/refresh', { method: 'POST'" in body
    assert body.index("/api/market/refresh") < body.index("ACC.reload(")
    assert "setWarm('warming', 'refresh')" in body and "setWarm('error', 'refresh')" in body
    view = _src("account/view-account.js")
    assert "onClick: () => ACC.refreshQuotes()" in view
    assert "'새로 받는 중'" in view and "'시세와 일봉을 새로 받지 못했어요'" in view


def test_back_from_a_lock_hides_the_page_until_checked():
    src = _src("account/core.js")
    assert "classList.add('is-rechecking')" in src and "recheck: true" in src
    assert ".is-rechecking #main" in _src("account/account.css")
    lock = src[src.index("ACC.lock = async function"):]
    lock = lock[:lock.index("\n  };")]
    assert lock.index("S.rememberAccount(null)") < lock.index("location.replace(")


def test_background_403_keeps_an_open_dialog():
    src = _src("account/core.js")
    fb = src[src.index("S.onForbidden = () =>"):]
    fb = fb[:fb.index("\n  };")]
    assert "document.querySelector('.s-scrim .s-dialog')" in fb
    assert "S.pinPrompt(" in fb and "showGate()" in fb


def test_coordinate_line_ages_with_the_analysis():
    src = _src("account/view-account.js")
    body = src[src.index("function coordLine(h)"):]
    body = body[:body.index("\n  }\n")]
    assert "S.analysisState(" in body and "as.state === 'old'" in body and "'overdue'" in body
    assert "' 분석'" in body
    assert "is-faded" in src and ".r-coords.is-faded" in _src("account/account.css")


def test_asset_trend_is_on_the_calendar_and_says_when_today_is_skipped():
    src = _src("account/view-account.js")
    assert "const x = i => xAt(dayMs(pts[i].date));" in src
    assert "n_missing > 0" in src and "시세 없는 종목이 있어 오늘 기록을 쓰지 않았어요" in src


def test_lock_clears_the_cookie_and_replaces_the_history_entry():
    src = _src("account/core.js")
    assert "ACC.path('/lock')" in src
    assert "location.replace('/?locked=' + encodeURIComponent(ACC.id))" in src
    assert "addEventListener('pageshow'" in src and "e.persisted" in src
    assert "addEventListener('visibilitychange'" in src
    assert "S.onForbidden = () =>" in src and "S.pinGate({" in src


def test_account_delete_asks_name_and_pin_and_says_what_goes():
    src = _src("account/settings.js")
    assert "method: 'DELETE'" in src and "JSON.stringify({ pin: pinVal })" in src
    assert "계좌와 그 안의 기록이 지워져요" in src
    assert "typed.value.trim() === name" in src
    assert "ACC.path('/pin')" in src and "current: cur, pin: nw" in src


def test_add_dialog_never_overwrites_a_held_row():
    src = _src("account/dialogs.js")
    assert "create_only: true" in src
    assert "예수금은 그대로예요" in src


def test_accounts_menu_shows_amounts_only_for_open_sessions():
    src = _src("common/accounts-menu.js")
    assert "/api/accounts/session" in src
    assert "gate: false" in src           # the menu's lock check never opens the page gate
    assert "localStorage" not in src      # amounts are never stored
    assert "window.AccountsMenu = {" in src and "open," in src and "openNew," in src


def test_chat_reply_is_rendered_as_text():
    src = _src("account/chat.js")
    assert "createTextNode" in src
    assert "isComposing" in src           # Korean IME: send after the commit
    assert "/chat/status?job_id=" in src


def test_settings_and_chat_follow_the_query():
    core = _src("account/core.js")
    assert "function syncPanels" in core
    assert "ACC.openPanel('settings')" in _src("account/settings.js")
    assert "ACC.openPanel('chat')" in _src("account/chat.js")


def test_each_holding_row_shows_its_pnl_amount_and_percent():
    """He asked for every holding's unrealized P&L in money, not only the %,
    whatever the market phase (2026-10-15)."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "src/dashboard_web/static/account/view-account.js").read_text()
    assert "const pnlAmt = h =>" in src and "(h.price - h.avg_cost) * h.qty" in src
    assert "S.fmtPnl(pa, c)" in src and "'r-pnl-pct'" in src
    assert "{ value: 'pnl_amt', label: '손익금액' }" in src
