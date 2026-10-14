"""종목 담당 화면 — SCR-STOCK (account/stock.js) · SCR-TRADES (account/trades.js) ·
DLG-TRADE (account/trade-dialog.js) · DLG-HOLD-FIX (account/hold-fix.js).

Read from source, no browser: the files register on the account page's
contract (account/core.js), keep the screen rules of ops notes §11 (tokens
only, canonical breakpoints, no style/on* attributes, one word per action),
and the writes keep their promises — one POST per trade with a look-before-
resend on failure, every field on a holding correction. The pure helpers
(받침 조사, 시장 마감 시각) run in node where it exists.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.dashboard_web import server

STATIC = Path(server.__file__).resolve().parent / "static"
ACC_DIR = STATIC / "account"
JS = ("stock.js", "trades.js", "trade-dialog.js", "hold-fix.js")
FILES = JS + ("stock.css",)
NODE = shutil.which("node")


def src(name: str) -> str:
    return (ACC_DIR / name).read_text()


def code_lines(name: str):
    """(line number, code) with // and /* */ comments dropped."""
    text = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), src(name), flags=re.DOTALL)
    for i, line in enumerate(text.splitlines(), 1):
        yield i, re.sub(r"(?<![:'\"])//.*$", "", line)


# ── 계약 ─────────────────────────────────────────────────────────────────
def test_files_register_on_the_account_contract():
    assert re.search(r"ACC\.views\.stock\s*=\s*\{\s*mount,\s*unmount,\s*update\s*\}", src("stock.js"))
    assert re.search(r"ACC\.views\.trades\s*=\s*\{\s*mount,\s*unmount,\s*update\s*\}", src("trades.js"))
    assert "ACC.openTrade = function" in src("trade-dialog.js")
    assert "ACC.openHoldFix = function" in src("hold-fix.js")
    page = (STATIC / "account.html").read_text()
    for f in FILES:
        assert f"<!--#include account/{f}-->" in page, f


def test_account_page_serves_the_stock_pieces_inline():
    c = TestClient(server.app)
    r = c.get("/a/junisa", headers={"host": "portfolio.jsyoo.dev"})
    assert r.status_code == 200
    for f in FILES:
        assert f'data-include="account/{f}"' in r.text, f


@pytest.mark.skipif(not NODE, reason="node not installed")
@pytest.mark.parametrize("name", JS)
def test_js_parses(name):
    r = subprocess.run([NODE, "--check", str(ACC_DIR / name)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr


# ── 화면 규칙 (ops notes §11) ───────────────────────────────────────────────
_HEX_VALUE = re.compile(r"[:=(,'\"`]\s*#[0-9a-fA-F]{3,8}(?![\w-])")
_RAW_RGBA = re.compile(r"\brgba?\(\s*\d")


@pytest.mark.parametrize("name", FILES)
def test_tokens_only(name):
    bad = [f"{name}:{i}: {line.strip()}" for i, line in code_lines(name)
           if _HEX_VALUE.search(line) or _RAW_RGBA.search(line)
           or re.search(r"font-size:\s*\d", line) or "--primitive-" in line
           or re.search(r"designColor\('primitive/", line)]
    assert not bad, "\n".join(bad)


def test_breakpoints_are_canonical_and_min_width_only():
    bad = []
    for i, line in code_lines("stock.css"):
        if "@media" in line:
            q = line.split("{", 1)[0]
            if "max-width" in q:
                bad.append(f"{i}: {line.strip()}")
            for px in re.findall(r"(\d+)px", q):
                if px not in {"600", "900", "1100"}:
                    bad.append(f"{i}: {line.strip()}")
    assert not bad, "\n".join(bad)


@pytest.mark.parametrize("name", JS)
def test_no_markup_strings_or_inline_handlers(name):
    """CSP: style="…" and on*= attributes are blocked; data never goes
    through innerHTML; the canonical el() keys are className / onClick."""
    bad = []
    for i, line in code_lines(name):
        if (re.search(r"\binnerHTML\b|insertAdjacentHTML|outerHTML\s*=", line)
                or re.search(r"""style\s*=\s*["']""", line)
                or re.search(r"""\son[a-z]+\s*=\s*["']""", line)
                or re.search(r"setAttribute\(\s*'style'", line)
                or re.search(r"(?<![\w-])(class|onclick)\s*:", line)
                or re.search(r"(?<![\w.])(confirm|alert|prompt)\(", line)):
            bad.append(f"{name}:{i}: {line.strip()}")
    assert not bad, "\n".join(bad)


@pytest.mark.parametrize("name", JS)
def test_one_word_per_action(name):
    """인계 §3.6: 삭제 · 거르기 풀기 · 버리기 — never 제거 / 해제 / 지우기 —
    and the shared verbs come from S.LABELS."""
    bad = [f"{name}:{i}: {line.strip()}" for i, line in code_lines(name)
           if any(w in line for w in ("제거", "해제", "지우기"))]
    assert not bad, "\n".join(bad)
    assert "LABELS" in src(name)


def test_no_declarative_da_endings_in_copy():
    """해요체: no '~다.' sentence in a string a person reads."""
    bad = []
    for name in JS:
        for i, line in code_lines(name):
            for s in re.findall(r"'([^'\n]*[가-힣][^'\n]*)'", line):
                if re.search(r"(습니다|[^요]다)(\.|$)", s.strip()) and not re.search(r"[가-힣]+다[가-힣]", s):
                    bad.append(f"{name}:{i}: {s}")
    assert not bad, "\n".join(bad)


# ── SCR-STOCK ────────────────────────────────────────────────────────────
def test_stock_chart_legend_and_coordinate_rules():
    s = src("stock.js")
    assert "' · % = 현재가 대비'" in s, "legend ends with % = 현재가 대비 (인계 §3.4)"
    assert "'평단 대비'" in s, "card % is against the average cost"
    assert "llm_only" in s and "' · 추정'" in s, "an ungrounded coordinate is dashed and says 추정"
    assert "LABELS.oldCoords" in s, "30일+ hides the coordinates behind 지난 좌표 보기"
    # 지지·저항 are computed levels: drawn whatever the analysis' age
    assert re.search(r"if \(on\.level\)", s)


def test_stock_reads_current_engine_schema():
    """Every path the 판정 card reads exists in both shapes the engine emits
    (a normal result and facts_only), and the renderer really reads its leaf."""
    from src.research.analyze import engine
    s = src("stock.js")
    m = re.search(r"const ANALYSIS_BODY_KEYS\s*=\s*\[([^\]]*)\]", s)
    assert m
    paths = re.findall(r"'([\w.]+)'", m.group(1))
    env = {"symbol": {"symbol_id": "KR:005930", "name": "삼성전자", "market": "KOSPI"},
           "asof": "2026-09-04", "domains": [], "available": []}
    shapes = {"ok": engine.normalize({}, env), "facts_only": engine._facts_only(env, "x")}
    rest = s.split("void ANALYSIS_BODY_KEYS;", 1)[1]
    for path in paths:
        for kind, body in shapes.items():
            cur = body
            for seg in path.split("."):
                assert isinstance(cur, dict) and seg in cur, f"{kind} lacks {path}"
                cur = cur[seg]
        assert re.search(rf"\.{path.split('.')[-1]}\b", rest), path
    # a facts_only result carries confidence 0.0 — never drawn as 신뢰도 dots
    assert "body.status === 'facts_only'" in rest


def test_stock_has_one_analysis_run_trigger():
    s = src("stock.js")
    assert len(re.findall(r"/run'", s)) == 1
    assert len(re.findall(r"function runAnalysis\(", s)) == 1
    assert "'보통 2~7분 · '" in s and "취소할 수 없어요" in s


def test_stock_peer_rank_and_etf_snapshot():
    """재무 타일의 '동종 n%' comes from peer.metrics[m].percentile (basis =
    the group), the own 3-year band only stands in; an ETF shows its
    NAV·괴리율·3개월 수익률·순자산 instead of statements."""
    s = src("stock.js")
    assert "peer.metrics[k].percentile" in s and "peer.basis" in s and "'동종 '" in s
    assert "band.per_pctile" in s and "band.pbr_pctile" in s
    etf = s[s.index("function paintEtf("):]
    etf = etf[:etf.index("function downloadCsv(")]
    for k in ("e.nav", "e.premium_pct", "e.return_3m_pct", "e.aum_eokwon", "'NAV'", "'괴리율'", "'3개월 수익률'", "'순자산'"):
        assert k in etf, k


def test_stock_facts_only_is_not_dressed_as_a_verdict():
    s = src("stock.js")
    branch = s[s.index("if (body.status === 'facts_only') {"):]
    branch = branch[:branch.index("return;")]
    assert "사실만" in branch and "stk-pill" not in branch and "confDots" not in branch


def test_stock_reads_shared_routes_and_never_writes_shared_data():
    """ops notes §4: the screen joins the account's numbers to the symbol's
    facts; the only writes are the account's own (holdings)."""
    s = src("stock.js")
    for route in ("/api/market/ohlcv/", "/api/analysis/analysis/", "/api/analysis/chart/",
                  "/api/analysis/fundamental/", "/api/analysis/news/", "/trades') + '?symbol_id="):
        assert route in s, route
    writes = re.findall(r"method:\s*'(POST|DELETE|PATCH|PUT)'", s)
    assert writes.count("POST") == 3 and writes.count("DELETE") == 2, writes   # run · 관심 추가 · 되돌리기 / 삭제 ×2
    assert "/api/market/refresh" not in s


def test_stock_layout_numbers():
    css = src("stock.css")
    assert "var(--layout-rail)" in css and "@media (min-width: 1100px)" in css, "≥1100 rail 240"
    # 390 fixed parts: top bar 48 + anchors 40 + bottom bar 64 = 152
    assert re.search(r"\.stk-anchors \{[^}]*height: 40px", css)
    assert re.search(r"\.stk-bar \{[^}]*height: calc\(64px", css)


# ── SCR-TRADES ───────────────────────────────────────────────────────────
def test_trades_filters_live_in_the_address():
    s = src("trades.js")
    assert "ACC.go(q, { keepScroll: true })" in s
    for k in ("since", "until", "symbol_id"):
        assert f"'{k}'" in s or f"q.{k}" in s or f"{k}=" in s, k
    assert "이 기간엔 거래가 없어요" in s and "아직 기록한 거래가 없어요" in s and "거래 이력을 불러오지 못했어요" in s
    assert "LABELS.clearFilter" in s


# ── DLG-TRADE ────────────────────────────────────────────────────────────
def test_trade_dialog_one_post_then_look_before_resend():
    s = src("trade-dialog.js")
    assert len(re.findall(r"method:\s*'POST'", s)) == 1
    assert "ACC.path('/trades')" in s
    verify = s[s.index("verify: async"):]
    verify = verify[:verify.index("},\n    });")]
    assert "'&limit=5'" in verify and "symbol_id=" in verify, "verify reads GET /trades?symbol_id=…&limit=5"
    assert "method" not in verify, "verify never writes"
    assert "account: ACC.accountRef()" in s, "403 → PIN 다시 입력 → 1회 재전송 (S.dialog)"
    assert "confirmDiscard: true" in s
    assert "DELETE" not in s and "/holdings" not in s, "one door for a trade: POST /trades"
    # a full sell takes the row on the server (C1): the screen reads holding.removed, never sends a flag
    assert "res.holding.removed" in s
    for gone in ("remove_when_flat", "plan_id", "trade_side", "sell_qty="):
        assert gone not in s, gone


def test_trade_dialog_states_and_words():
    s = src("trade-dialog.js")
    for words in ("휴장일이에요 · 직전 거래일 ", "전량 매도라 목록에서 빠져요", "거래 이력에는 남아요",
                  "예수금이 마이너스가 돼요", "보다 많아요", "기록됐는지 확인 중", "매수 수수료는 평단에 없어요",
                  "이번 매도 손익", "전량 매도를 기록했어요 · 거래 이력에 남아요", "LABELS.viewTrade"):
        assert words in s, words
    assert "max: t" in s, "future dates blocked"
    # a past day is stamped at that market's close
    assert "CLOSE = { KR: [15, 30], US: [16, 0] }" in s


# ── DLG-HOLD-FIX ─────────────────────────────────────────────────────────
def test_hold_fix_sends_every_field_and_blocks_zero():
    s = src("hold-fix.js")
    payload = re.search(r"const payload = \{(.*?)\};", s, re.DOTALL).group(1)
    for k in ("symbol_id", "qty", "avg_cost", "note", "name"):
        assert re.search(rf"\b{k}:", payload), k
    assert "0주로 만들려면 매도 기록이나 종목 삭제를 써요" in s
    assert "거래 기록 없이 숫자만 바꿔요 · 예수금 그대로" in s


# ── 순수 도우미 (node) ───────────────────────────────────────────────────
HARNESS = r"""
const vm = require('vm'), fs = require('fs');
const window = { __THEME_TOKENS: {} };
const document = { addEventListener() {}, body: null };
const ctx = { window, document, Intl, Date, Math, JSON, Promise, setTimeout, console, matchMedia: () => ({ matches: true }) };
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), ctx);
window.ACC = { views: {}, on() { return () => {}; } };
vm.runInNewContext(fs.readFileSync(process.argv[2], 'utf8'), ctx);
const k = window.ACC.stk;
process.stdout.write(JSON.stringify([
  k.josa('삼성전자', '을/를'), k.josa('LG이노텍', '을/를'), k.josa('준ISA', '로'), k.josa('서울', '로'),
  k.josa('한국', '로'), k.josa('KR:000000', '은/는'),
  k.zonedEpoch('2026-10-13', 15, 30, 'Asia/Seoul'), k.zonedEpoch('2026-07-15', 16, 0, 'America/New_York'),
  k.zonedEpoch('2026-12-15', 16, 0, 'America/New_York'),
  k.barDate(1791817200, 'KR'), k.split('US:BRK.B'), k.addMonths('2026-10-15', -3),
]));
"""


@pytest.mark.skipif(not NODE, reason="node not installed")
def test_pure_helpers():
    r = subprocess.run([NODE, "-e", HARNESS, str(STATIC / "shared.js"), str(ACC_DIR / "stock.js")],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out[:6] == ["삼성전자를", "LG이노텍을", "준ISA로", "서울로", "한국으로", "KR:000000은"]
    assert out[6] == 1791873000                      # 2026-10-13 15:30 KST = 06:30 UTC
    assert out[7] == 1784145600                      # 16:00 EDT = 20:00 UTC
    assert out[8] == 1797368400                      # 16:00 EST = 21:00 UTC
    assert out[9] == "2026-10-13"
    assert out[10] == ["US", "BRK.B"]
    assert out[11] == "2026-07-15"
