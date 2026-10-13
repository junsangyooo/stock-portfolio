"""The account snapshot the chat agent gets on every turn.

Each holding's line carries the stored analysis's opinion — label, date and
coordinates. It used to read a field (`action_ko`) the summary never had, so
the opinion was missing from every snapshot.
"""
from __future__ import annotations

import re

from src.portfolio.chat import context
from src.core import paths
from src.research import analyze

_V = {"label": "buy", "status": "ok", "date": "2026-10-10",
      "coords": {"stop": 9000, "target": 12000,
                 "entry_low": 9500, "entry_high": 9800}}


def test_a_holding_gets_label_date_stop_and_target():
    line = context._opinion(_V, held=True, price=10000, ccy="KRW")
    assert line == " | 종합의견 긍정 (2026-10-10) · 손절 ₩9,000 (-10.0%) · 목표 ₩12,000 (+20.0%)"


def test_a_watch_row_also_gets_the_entry_zone():
    line = context._opinion(_V, held=False, price=10000, ccy="KRW")
    assert "진입구간 ₩9,500~₩9,800 · 손절" in line


def test_facts_only_is_not_dressed_as_an_opinion():
    v = {**_V, "status": "facts_only", "label": "neutral"}
    assert context._opinion(v, True, 10000, "KRW") == " | 종합의견 판단 없음(사실만, 2026-10-10)"


def test_nothing_stored_says_nothing():
    assert context._opinion({"date": None, "label": None}, True, 10000, "KRW") == ""
    assert context._opinion({}, True, 10000, "KRW") == ""


def test_no_price_still_gives_the_coordinates():
    line = context._opinion(_V, held=True, price=None, ccy="USD")
    assert "손절 $9,000.00 · 목표 $12,000.00" in line


def test_the_snapshot_carries_the_opinion(monkeypatch):
    monkeypatch.setattr(context.service, "aggregate", lambda aid, spark_n=2: {
        "exists": True, "totals": {},
        "holdings": [{"symbol_id": "KR:005930", "name_kr": "삼성전자",
                      "currency": "KRW", "price": 10000, "avg_cost": 8000,
                      "qty": 3}]})
    monkeypatch.setattr(analyze, "summary", lambda sids: {"KR:005930": _V})
    ctx = context.account_context("alice")
    assert "종합의견 긍정 (2026-10-10) · 손절 ₩9,000 (-10.0%)" in ctx


def test_label_words_match_the_badge():
    js = (paths.ROOT / "src/dashboard_web/static/shared.js").read_text(encoding="utf-8")
    block = re.search(r"const LABEL_KO = \{(.*?)\};", js, re.S).group(1)
    assert dict(re.findall(r"(\w+): '([^']+)'", block)) == context.LABEL_KO
