"""The chat agent's lookup tools: one account, every article.

The account commands read the account the server bound the process to, and
nothing else: no command takes an account id, and the binding is signed so a
process that names another account in its environment is refused. The news
commands read the whole archive, untagged articles included.
"""
from __future__ import annotations

import ast
import inspect
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.portfolio import security
from src.core.kst import KST
from src.portfolio.chat import tools
from src.portfolio.store import (account_store, balance_store, chat_store,
                                 holding_store, snapshot_store, trade_store)
from src.research.news.db import connect as news_connect

_SRC = Path(__file__).resolve().parents[1] / "src"


def _bind(monkeypatch, aid, sig_for=None):
    monkeypatch.setenv("PDASH_CHAT_ACCOUNT", aid)
    monkeypatch.setenv("PDASH_CHAT_SIG", security.chat_binding(sig_for or aid))


def _run(capsys, *argv):
    assert tools.main(list(argv)) == 0
    return json.loads(capsys.readouterr().out)


@pytest.fixture
def two_books():
    for aid in ("alice", "bob"):
        account_store.create(aid, aid, "GENERAL")
        account_store.set_pin_hash(aid, security.hash_pin(aid, "123456"))
        holding_store.upsert(aid, f"KR:00000{1 if aid == 'alice' else 2}",
                             qty=10, avg_cost=1000)
        balance_store.set_many(aid, {"KRW": 5000 if aid == "alice" else 9999})
        trade_store.record(aid, symbol_id="KR:005930", side="buy", qty=1,
                           price=70000 if aid == "alice" else 1, currency="KRW")
        snapshot_store.write(datetime.now(KST).strftime("%Y-%m-%d"), aid,
                             {"KRW": {"eval": 1, "cost": 1, "pnl": 0,
                                      "cash": 0, "total_assets": 1}})
    return {aid: chat_store.create(aid, f"{aid}의 대화") for aid in ("alice", "bob")}


# ── the binding ────────────────────────────────────────────────────────────

ACCOUNT_CMDS = ("account", "trades", "snapshots", "chats")


@pytest.mark.parametrize("cmd", ACCOUNT_CMDS)
def test_no_binding_is_refused(capsys, monkeypatch, cmd, two_books):
    monkeypatch.delenv("PDASH_CHAT_ACCOUNT", raising=False)
    monkeypatch.delenv("PDASH_CHAT_SIG", raising=False)
    assert "PermissionError" in _run(capsys, cmd)["error"]


@pytest.mark.parametrize("cmd", ACCOUNT_CMDS)
def test_naming_another_account_without_its_signature_is_refused(
        capsys, monkeypatch, cmd, two_books):
    """The agent's own signature does not open bob's book."""
    _bind(monkeypatch, "bob", sig_for="alice")
    assert "PermissionError" in _run(capsys, cmd)["error"]
    monkeypatch.setenv("PDASH_CHAT_SIG", "0" * 64)
    assert "PermissionError" in _run(capsys, cmd)["error"]


def test_no_command_takes_an_account():
    for name, fn in tools._COMMANDS.items():
        for p in inspect.signature(fn).parameters:
            assert "account" not in p, (name, p)


def test_an_account_option_is_rejected(capsys, monkeypatch, two_books):
    _bind(monkeypatch, "alice")
    assert "error" in _run(capsys, "trades", "account=bob")


# ── one account's book ─────────────────────────────────────────────────────

def test_account_is_this_account_without_its_pin_hash(capsys, monkeypatch, two_books):
    _bind(monkeypatch, "alice")
    out = _run(capsys, "account")
    assert out["account"]["account_id"] == "alice"
    assert "pin_hash" not in out["account"]
    held = {h["symbol_id"] for h in out["holdings"]}
    assert "KR:000001" in held and "KR:000002" not in held     # bob's
    assert out["cash"]["KRW"] != 9999


def test_trades_are_this_accounts_only(capsys, monkeypatch, two_books):
    _bind(monkeypatch, "alice")
    out = _run(capsys, "trades")
    assert {t["account_id"] for t in out["trades"]} == {"alice"}
    assert [t["price"] for t in out["trades"]] == [70000]


def test_trades_page_with_offset(capsys, monkeypatch, two_books):
    for i in range(4):
        trade_store.record("alice", symbol_id="KR:000001", side="buy", qty=1,
                           price=100 + i, currency="KRW",
                           traded_at=int(datetime.now(KST).timestamp()) - i)
    _bind(monkeypatch, "alice")
    p1 = _run(capsys, "trades", "symbol=kr:000001", "limit=3")
    assert p1["returned"] == 3 and p1["next_offset"] == 3
    p2 = _run(capsys, "trades", "symbol=KR:000001", "limit=3", "offset=3")
    assert p2["returned"] == 1 and p2["next_offset"] is None


def test_snapshots_are_this_accounts_only(capsys, monkeypatch, two_books):
    _bind(monkeypatch, "alice")
    out = _run(capsys, "snapshots", "currency=krw", "days=30")
    assert out["n"] == 1 and out["currency"] == "KRW"


def test_chats_list_and_read_this_account_only(capsys, monkeypatch, two_books):
    _bind(monkeypatch, "alice")
    assert [c["title"] for c in _run(capsys, "chats")["chats"]] == ["alice의 대화"]
    assert _run(capsys, "chat", two_books["alice"])["session_id"] == two_books["alice"]
    # bob's thread id, asked from alice's chat
    assert "error" in _run(capsys, "chat", two_books["bob"])


# ── shapes that become paths ───────────────────────────────────────────────

@pytest.mark.parametrize("argv", [
    ("verdict", "KR:005930", "../../../../.claude/.credentials"),
    ("verdict", "KR:../../.."),
    ("chart", "KR:x/y"),
])
def test_paths_cannot_walk_out(capsys, argv):
    assert "ValueError" in _run(capsys, *argv)["error"]


def test_verdict_reads_a_stored_day(capsys, monkeypatch):
    from src.research import analyze
    monkeypatch.setattr(analyze, "on", lambda sid, d: {"date": d, "label": "buy"})
    out = _run(capsys, "verdict", "KR:005930", "2026-10-01")
    assert out["exists"] and out["result"]["date"] == "2026-10-01"


# ── the archive ────────────────────────────────────────────────────────────

def _article(c, i, title, day, source="src_a", themes=(), body=None, imp=3):
    c.execute(
        "INSERT INTO signals (dedup_key, url, title, body, source_id, source_group, "
        "lead_lag, fetched_at, collected_on, summary, importance) "
        "VALUES (?, ?, ?, ?, ?, 'news', 'lagging', ?, ?, ?, ?)",
        (f"k{i}", f"http://x/{i}", title, body, source, day + "T00:00:00",
         day, f"요약 {i}", imp))
    sid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    for t in themes:
        c.execute("INSERT INTO signal_themes (signal_id, theme_id, confidence) "
                  "VALUES (?, ?, 0.8)", (sid, t))
    return sid


@pytest.fixture
def archive():
    today = datetime.now(KST).date()
    d = [(today - timedelta(days=i)).isoformat() for i in range(3)]
    with news_connect() as c:
        ids = [
            _article(c, 1, "반도체 수출 증가", d[0], themes=("semiconductor",), imp=5),
            _article(c, 2, "지역 축제 소식", d[1]),                       # untagged
            _article(c, 3, "금리 동결", d[2], source="bok",
                     themes=("macro",), body="한국은행이 반도체 경기를 언급"),
        ]
        c.execute("INSERT INTO theme_weekly (theme_id, week_start, summary, bullets, "
                  "n_days, built_at) VALUES ('semiconductor', '2026-10-05', '주간', "
                  "'[\"a\"]', 5, 'x')")
        c.execute("INSERT INTO source_cursor (source_id, last_ok_at, n_consecutive_fail) "
                  "VALUES ('etnews_it', '2026-10-13', 2)")
        c.commit()
    return ids


def test_search_finds_untagged_and_body_words(capsys, archive):
    assert [i["title"] for i in _run(capsys, "news_search", "theme=none")["items"]] \
        == ["지역 축제 소식"]
    hit = _run(capsys, "news_search", "반도체")
    assert hit["total"] == 2                      # a title and a body
    assert _run(capsys, "news_search", "source=bok")["items"][0]["themes"][0]["theme_id"] == "macro"
    assert _run(capsys, "news_search", "min_importance=5")["total"] == 1


def test_search_pages(capsys, archive):
    p1 = _run(capsys, "news_search", "limit=2")
    assert p1["total"] == 3 and p1["returned"] == 2 and p1["next_offset"] == 2
    p2 = _run(capsys, "news_search", "limit=2", "offset=2")
    assert p2["returned"] == 1 and p2["next_offset"] is None
    assert p1["items"][0]["title"] == "반도체 수출 증가"     # newest first


def test_article_has_every_field_and_its_themes(capsys, archive):
    out = _run(capsys, "article", str(archive[0]))
    assert out["title"] == "반도체 수출 증가" and out["dedup_key"] == "k1"
    assert out["themes"] == [{"theme_id": "semiconductor", "confidence": 0.8}]
    assert "error" in _run(capsys, "article", "999999")


def test_theme_history_by_kind(capsys, archive):
    out = _run(capsys, "theme_history", "semiconductor", "kind=weekly")
    assert out["items"][0]["bullets"] == ["a"] and out["total"] == 1
    assert "ValueError" in _run(capsys, "theme_history", "semiconductor", "kind=hourly")["error"]


def test_catalog_lists_themes_and_source_status(capsys, archive):
    out = _run(capsys, "catalog")
    assert len(out["themes"]) >= 23
    assert all(t["definition"] for t in out["themes"])
    et = [s for s in out["sources"] if s["source_id"] == "etnews_it"]
    assert et and et[0]["n_consecutive_fail"] == 2


def test_symbol_news_includes_macro_for_the_agent(monkeypatch):
    from src.market.store import theme_store
    from src.research.news import read
    monkeypatch.setattr(theme_store, "get", lambda sid: [
        {"theme_id": "macro", "relation": "macro", "weight": 0.2},
        {"theme_id": "semiconductor", "relation": "primary", "weight": 1.0}])
    agent = read.symbol_news("KR:005930", include_macro=True)
    tab = read.symbol_news("KR:005930")
    assert {t["theme_id"] for t in agent["themes"]} == {"macro", "semiconductor"}
    assert {t["theme_id"] for t in tab["themes"]} == {"semiconductor"}


def test_mentions_are_credited_to_the_named_symbol_only(monkeypatch):
    """'한화' must not take a '한화에어로스페이스' headline."""
    from src.research.news import reverse
    monkeypatch.setattr(reverse, "_name_entries", lambda: [
        ("한화에어로스페이스", "KR:012450"), ("한화", "KR:000880")])
    today = datetime.now(KST).date().isoformat()
    with news_connect() as c:
        _article(c, 10, "한화에어로스페이스 수주", today)
        _article(c, 11, "한화 지분 매각", today)
        c.commit()
    assert [m["title"] for m in reverse.mention_items("KR:000880")] == ["한화 지분 매각"]
    assert [m["title"] for m in reverse.mention_items("KR:012450")] == ["한화에어로스페이스 수주"]


# ── layers ────────────────────────────────────────────────────────────────

def test_lower_layers_never_import_the_router():
    """정의서 §1.1 규칙 2: research and portfolio sit below api."""
    for root in ("research", "portfolio", "market", "core", "universe"):
        for path in (_SRC / root).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    assert not (mod == "api" or mod.startswith("api.")
                                or mod.startswith("src.api")), path
