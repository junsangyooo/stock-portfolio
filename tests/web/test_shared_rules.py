"""The pure helpers in shared.js — number/date formats (인계 §3.1), the
손익 label (§3.2), quote state (§3.3), analysis state (§3.4), mood bands
(§3.5) — run in node against fixed inputs. Skipped where node is absent
(the server has none; the laptop has)."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from src.dashboard_web import server

STATIC = Path(server.__file__).resolve().parent / "static"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(not NODE, reason="node not installed")

HARNESS = r"""
const vm = require('vm');
const src = require('fs').readFileSync(process.argv[1], 'utf8');
const window = { __THEME_TOKENS: JSON.parse(process.argv[2]) };
const document = { addEventListener() {}, body: null };
vm.runInNewContext(src, { window, document, Intl, Date, Math, JSON, Promise, setTimeout, console });
const S = window.S;
const cases = JSON.parse(process.argv[3]);
const out = cases.map(([fn, args]) => {
  try { return S[fn](...args); } catch (e) { return 'THROW ' + e.message; }
});
process.stdout.write(JSON.stringify(out));
"""


def run(cases):
    tokens = server._theme_style_tag().split("window.__THEME_TOKENS = ", 1)[1].rsplit(";</script>", 1)[0]
    r = subprocess.run([NODE, "-e", HARNESS, str(STATIC / "shared.js"), tokens, json.dumps(cases)],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


M = "\u2212"


def test_number_formats():
    cases = [
        ("fmtPrice", [74800, "KRW"]), ("fmtPrice", [198.4, "USD"]), ("fmtPrice", [None, "KRW"]),
        ("fmtMoney", [54338800, "KRW"]), ("fmtMoney", [12340.564, "USD"]), ("fmtMoney", [-1500, "KRW"]),
        ("fmtPnl", [1982400, "KRW"]), ("fmtPnl", [-120000, "KRW"]), ("fmtPnl", [0, "KRW"]),
        ("fmtPnl", [-0.004, "USD"]), ("fmtPnl", [12.5, "USD"]),
        ("fmtPnlPct", [31.2]), ("fmtPnlPct", [-1]), ("fmtPnlPct", [0.78, 2]),
        ("fmtChange", [4900, "KRW"]), ("fmtChange", [-1200, "KRW"]), ("fmtChange", [0, "KRW"]),
        ("fmtChangePct", [2.53]), ("fmtChangePct", [-1.06]), ("fmtChangePct", [0]),
        ("fmtQty", [12]), ("fmtQty", [0.5]), ("fmtQty", [1234.56789]), ("fmtQty", [3.1000]),
        ("fmtAxisMan", [56000000]), ("fmtAxisMan", [44000000]), ("fmtCount", [4296]),
        ("trend", [3]), ("trend", [-2]), ("trend", [0]),
    ]
    assert run(cases) == [
        "74,800", "198.40", "—",
        "₩54,338,800", "$12,340.56", M + "₩1,500",
        "+₩1,982,400", M + "₩120,000", "₩0",
        "$0.00", "+$12.50",
        "+31.2%", M + "1.0%", "+0.78%",
        "▲4,900", "▼1,200", "0",
        "▲2.53%", "▼1.06%", "0.00%",
        "12", "0.5", "1,234.5679", "3.1",
        "5,600만", "4,400만", "4,296",
        "up", "down", "flat",
    ]


def test_dates_are_kst():
    cases = [
        ("fmtDate", ["2026-10-13"]), ("fmtDate", ["2026-10-08"]),
        ("fmtDateTime", ["2026-10-13T06:42:00Z"]), ("fmtDateTime", [1789000000]),
        ("fmtDate", ["2026-10-13T16:00:00Z"]),                 # 01:00 KST next day
        ("fmtDate", [None]),
        ("tradingDaysBetween", ["2026-10-09", "2026-10-13"]),  # Fri → Tue = Mon, Tue
        ("marketPhase", ["KR", "2026-10-13T01:00:00Z"]),        # 10:00 KST Tue
        ("marketPhase", ["KR", "2026-10-13T07:00:00Z"]),        # 16:00 KST
        ("marketPhase", ["KR", "2026-10-11T01:00:00Z"]),        # Sunday
        ("marketPhase", ["US", "2026-10-13T14:00:00Z"]),        # 10:00 New York (EDT)
    ]
    out = run(cases)
    assert out[:3] == ["10/13(화)", "10/8(목)", "10/13(화) 15:42"]
    assert re.fullmatch(r"\d{1,2}/\d{1,2}\([일월화수목금토]\) \d\d:\d\d", out[3])
    assert out[4:] == ["10/14(수)", "", 2, "open", "closed", "holiday", "open"]


def test_pnl_label():
    cases = [
        ("pnlLabel", [{"phase": "open", "session": "2026-10-13", "today": "2026-10-13"}]),
        ("pnlLabel", [{"phase": "closed", "session": "2026-10-13", "today": "2026-10-13"}]),
        ("pnlLabel", [{"phase": "closed", "session": "2026-10-12", "today": "2026-10-13"}]),
        ("pnlLabel", [{"phase": "holiday", "session": "2026-10-09", "today": "2026-10-11"}]),
    ]
    assert run(cases) == [
        {"pnl": "오늘 손익 · 장중", "head": "현재가 · 오늘", "day": "오늘"},
        {"pnl": "오늘 손익 · 마감", "head": "현재가 · 오늘", "day": "오늘"},
        {"pnl": "10/12(월) 손익 · 마감", "head": "현재가 · 10/12(월)", "day": "10/12(월)"},
        {"pnl": "10/9(금) 손익 · 휴장", "head": "현재가 · 10/9(금)", "day": "10/9(금)"},
    ]


def test_quote_state():
    now = "2026-10-13T03:00:00Z"                 # 12:00 KST Tue, market open
    base = {"market": "KR", "session": "2026-10-13", "phase": "open", "now": now}
    cases = [
        ("quoteState", [dict(base, quoteTs="2026-10-13T02:55:00Z")]),   # 5 min
        ("quoteState", [dict(base, quoteTs="2026-10-13T02:30:00Z")]),   # 30 min
        ("quoteState", [dict(base, quoteTs="2026-10-12T06:30:00Z")]),   # Mon 15:30 → 1 day
        ("quoteState", [dict(base, quoteTs="2026-10-08T06:30:00Z")]),   # Thu → 3 trading days
        ("quoteState", [dict(base, quoteTs="2026-10-01T06:30:00Z")]),   # 12 days → none
        ("quoteState", [dict(base, quoteTs=None)]),
        # received 07:00 KST Tue = Monday's close; after the Tuesday close it is stale
        ("quoteState", [{"market": "KR", "session": "2026-10-13", "phase": "closed",
                         "now": "2026-10-13T08:00:00Z", "quoteTs": "2026-10-12T22:00:00Z"}]),
    ]
    out = run(cases)
    assert [o["state"] for o in out] == ["fresh", "delayed", "stale", "stale", "none", "none", "stale"]
    assert out[1]["text"] == "30분 전" and out[1]["inToday"] is True
    assert out[2]["text"] == "1거래일 전" and out[2]["inTotal"] is True and out[2]["inToday"] is False
    assert out[3]["tradingDays"] == 3
    assert out[4]["inTotal"] is False and out[4]["text"] == "시세 없음"


def test_analysis_state():
    now = "2026-10-13T03:00:00Z"
    c = lambda **k: ("analysisState", [dict({"now": now, "held": True}, **k)])
    out = run([
        c(date="2026-10-13"), c(date="2026-09-29"), c(date="2026-09-28"), c(date="2026-09-13"),
        c(date="2026-09-12"), c(date=None), c(date="2026-10-10", running=True),
        c(date="2026-10-10", failed=True), c(date="2026-10-10", hung=True),
        c(date="2026-10-10", status="facts_only"),
        c(date="2026-10-01", held=False), c(date="2026-08-01", held=False),
        ("depthLabel", ["low"]), ("depthLabel", [None]), ("verdictTone", ["strong_sell"]),
    ])
    assert [o["state"] for o in out[:12]] == [
        "fresh", "fresh", "overdue", "overdue", "old", "none", "running",
        "failed", "hung", "facts", "watch", "watch"]
    assert out[1]["days"] == 14 and out[2]["days"] == 15 and out[3]["days"] == 30 and out[4]["days"] == 31
    assert [o["line"] for o in out[:5]] == ["solid", "solid", "faded", "faded", "hidden"]
    assert out[2]["badge"] == "갱신 누락" and out[10]["badge"] == "자동 분석 없음"
    assert out[6]["keepPrevious"] is True and out[10]["line"] == "faded" and out[11]["line"] == "hidden"
    assert out[12:] == ["빠른 분석", "정밀 분석", "negative"]


def test_mood_band():
    out = run([("moodBand", [s]) for s in (0.30, 0.2999, 0.10, 0.0999, 0, -0.10, -0.30, -0.8, None)]
              + [("moodBand", [0.5, {"error": True}])])
    assert [o["band"] for o in out] == [
        "positive-strong", "positive", "positive", "neutral", "neutral",
        "negative", "negative-strong", "negative-strong", "none", "error"]
    assert [o["arrow"] for o in out[:7]] == ["▲", "▲", "▲", "▶", "▶", "▼", "▼"]
    assert out[0]["token"] == "mood/positive-strong" and out[9]["token"] == "skeleton/fill"


def test_labels_and_tokens_reach_js():
    out = run([("designColor", ["market/up"]), ("designColor", ["accent/default"]),
               ("designColor", ["cyan"]), ("designToken", ["bp/sm"]), ("designToken", ["space-1"]),
               ("cssVar", ["type/heading-1/size"]), ("newsThemeIcon", ["bio"]), ("newsThemeIcon", ["?"]),
               ("srcName", ["thelec_semi"]), ("srcName", ["no_such_feed"])])
    # the old Ink wash names ('cyan', 'space-1') no longer resolve: text/primary · ''
    assert out == ["#D42A36", "#0B7A6B", "#191F28", "600px", "", "--type-heading-1-size",
                   "heart-pulse", "newspaper", "디일렉", "no_such_feed"]
