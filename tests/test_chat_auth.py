"""Chat is per-account, not per-session.

The reply embeds one account's holdings, PnL and cash, so the server-wide /api
session gate is not enough — a session for account A must not read account B.
The SQLite DB is redirected to a tmp file so tests never touch data/.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import chat
from src.core import security
from src.portfolio.store import account_store, db as pdb


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(pdb, "DB_PATH", str(tmp_path / "chat.db"))
    monkeypatch.setattr(pdb, "_initialized", False)
    monkeypatch.setattr(security, "_SECRET_PATH", tmp_path / ".session_secret",
                        raising=False)

    for aid in ("alice", "bob"):
        account_store.create(aid, aid, "GENERAL")
        account_store.set_pin_hash(aid, security.hash_pin(aid, "123456"))

    app = FastAPI()
    app.include_router(chat.router)
    return TestClient(app)


def _body():
    return {"messages": [{"role": "user", "content": "내 포트폴리오 어때?"}]}


def test_other_accounts_session_is_rejected(client, monkeypatch):
    called = []
    monkeypatch.setattr(chat, "_account_context",
                        lambda aid: called.append(aid) or "")
    cookie = {security.COOKIE_PREFIX + "alice": security.make_session_token("alice")}
    r = client.post("/api/accounts/bob/chat", json=_body(), cookies=cookie)
    assert r.status_code == 403
    assert called == [], "another account's data must never be assembled"


def test_no_cookie_is_rejected(client):
    r = client.post("/api/accounts/alice/chat", json=_body())
    assert r.status_code == 403


def test_own_session_reaches_the_handler(client, monkeypatch):
    monkeypatch.setattr(chat, "_account_context", lambda aid: f"[계좌] {aid}")
    monkeypatch.setattr(chat, "_news_context", lambda: "")
    seen = {}

    class _Stub:
        def __init__(self, *a, **kw):
            pass

        def call(self, system, user, timeout_s=60, model=None):
            seen["user"] = user
            return "네, 괜찮습니다."

    monkeypatch.setattr(chat, "ClaudeCliBackend", _Stub)
    cookie = {security.COOKIE_PREFIX + "alice": security.make_session_token("alice")}
    r = client.post("/api/accounts/alice/chat", json=_body(), cookies=cookie)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "reply": "네, 괜찮습니다."}
    assert "[계좌] alice" in seen["user"]


def test_empty_conversation_short_circuits(client, monkeypatch):
    monkeypatch.setattr(chat, "ClaudeCliBackend",
                        lambda *a, **kw: pytest.fail("must not spend a call"))
    cookie = {security.COOKIE_PREFIX + "alice": security.make_session_token("alice")}
    r = client.post("/api/accounts/alice/chat", json={"messages": []}, cookies=cookie)
    assert r.json()["ok"] is False
