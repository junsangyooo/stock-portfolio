"""Chat is per-account, not per-session, and threads are async jobs.

The reply embeds one account's holdings, PnL and cash, so the server-wide /api
session gate is not enough — a session for account A must not read account B.
That boundary now covers the thread routes too: a session_id is only reachable
through the account that owns it.

The SQLite DB is redirected to a tmp file so tests never touch data/.
"""
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import chat
from src.core import jobs
from src.portfolio.chat import context, prompt, session
from src.portfolio import security
from src.portfolio.store import account_store, db as pdb


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(pdb, "DB_PATH", str(tmp_path / "chat.db"))
    monkeypatch.setattr(pdb, "_initialized", False)
    monkeypatch.setattr(session, "RUNNER", jobs.Runner(stale_s=1800))
    # No CLI transcript on disk in tests → every turn is a first turn unless a
    # case says otherwise.
    monkeypatch.setattr(session, "cli_session_exists", lambda sid: False)

    for aid in ("alice", "bob"):
        account_store.create(aid, aid, "GENERAL")
        account_store.set_pin_hash(aid, security.hash_pin(aid, "123456"))

    app = FastAPI()
    app.include_router(chat.router)
    return TestClient(app)


def _cookie(aid="alice"):
    return {security.COOKIE_PREFIX + aid: security.make_session_token(aid)}


def _stub_backend(monkeypatch, reply="네, 괜찮습니다.", seen=None):
    class _Stub:
        def __init__(self, *a, **kw):
            pass

        def call(self, system, user, model=None, extra_args=None, cwd=None,
                 env_extra=None):
            if seen is not None:
                seen.update(system=system, user=user, extra_args=extra_args)
            return reply

    monkeypatch.setattr(session, "ClaudeCliBackend", _Stub)


def _wait(client, aid, job_id, timeout=5.0):
    """Poll until the background thread settles."""
    end = time.time() + timeout
    while time.time() < end:
        d = client.get(f"/api/accounts/{aid}/chat/status",
                       params={"job_id": job_id}, cookies=_cookie(aid)).json()
        if d["state"] != "running":
            return d
        time.sleep(0.02)
    pytest.fail("job never finished")


def _new_thread(client, aid="alice"):
    r = client.post(f"/api/accounts/{aid}/chats", cookies=_cookie(aid))
    assert r.status_code == 200
    return r.json()["session_id"]


# ── account boundary ──────────────────────────────────────────────────

def test_other_accounts_session_is_rejected(client, monkeypatch):
    called = []
    monkeypatch.setattr(context, "account_context",
                        lambda aid: called.append(aid) or "")
    r = client.post("/api/accounts/bob/chat", cookies=_cookie("alice"),
                    json={"session_id": "x", "message": "내 포트폴리오 어때?"})
    assert r.status_code == 403
    assert called == [], "another account's data must never be assembled"


def test_no_cookie_is_rejected(client):
    for method, path in (("POST", "/api/accounts/alice/chat"),
                         ("GET", "/api/accounts/alice/chats"),
                         ("POST", "/api/accounts/alice/chats"),
                         ("GET", "/api/accounts/alice/chats/x"),
                         ("DELETE", "/api/accounts/alice/chats/x")):
        r = client.request(method, path,
                           json={"session_id": "x", "message": "hi"})
        assert r.status_code == 403, path


def test_thread_of_another_account_reads_as_missing(client):
    sid = _new_thread(client, "bob")
    # alice is authenticated for her own account but must not reach bob's thread
    assert client.get(f"/api/accounts/alice/chats/{sid}",
                      cookies=_cookie("alice")).status_code == 404
    assert client.delete(f"/api/accounts/alice/chats/{sid}",
                         cookies=_cookie("alice")).status_code == 404
    assert client.post("/api/accounts/alice/chat", cookies=_cookie("alice"),
                       json={"session_id": sid, "message": "hi"}).status_code == 404
    # still bob's, untouched
    assert client.get(f"/api/accounts/bob/chats/{sid}",
                      cookies=_cookie("bob")).status_code == 200


# ── ask / poll round trip ─────────────────────────────────────────────

def test_answer_lands_in_the_thread(client, monkeypatch):
    monkeypatch.setattr(context, "account_context", lambda aid: f"[계좌] {aid}")
    monkeypatch.setattr(context, "news_context", lambda: "")
    seen = {}
    _stub_backend(monkeypatch, seen=seen)

    sid = _new_thread(client)
    r = client.post("/api/accounts/alice/chat", cookies=_cookie(),
                    json={"session_id": sid, "message": "내 포트폴리오 어때?"})
    assert r.status_code == 200 and r.json()["ok"] is True
    d = _wait(client, "alice", r.json()["job_id"])
    assert d["state"] == "done" and d["reply"] == "네, 괜찮습니다."

    assert "[계좌] alice" in seen["user"]
    assert seen["system"] == prompt.SYSTEM, "first turn carries the system prompt"
    # No turn cap: the agent loop must be able to run as long as it needs.
    assert "--max-turns" not in seen["extra_args"]
    assert seen["extra_args"] == prompt.TOOL_ARGS + ["--session-id", sid]

    thread = client.get(f"/api/accounts/alice/chats/{sid}",
                        cookies=_cookie()).json()["chat"]
    assert [m["role"] for m in thread["messages"]] == ["user", "assistant"]
    assert thread["title"] == "내 포트폴리오 어때?"


def test_second_turn_resumes_the_cli_session(client, monkeypatch):
    monkeypatch.setattr(context, "account_context", lambda aid: "[계좌] alice")
    monkeypatch.setattr(context, "news_context", lambda: "")
    seen = {}
    _stub_backend(monkeypatch, seen=seen)

    sid = _new_thread(client)
    r = client.post("/api/accounts/alice/chat", cookies=_cookie(),
                    json={"session_id": sid, "message": "한중엔시에스 왜 줄이래?"})
    _wait(client, "alice", r.json()["job_id"])

    # the first turn's run created the CLI transcript
    monkeypatch.setattr(session, "cli_session_exists", lambda s: True)
    r = client.post("/api/accounts/alice/chat", cookies=_cookie(),
                    json={"session_id": sid, "message": "그럼 피엔티는?"})
    _wait(client, "alice", r.json()["job_id"])

    assert seen["extra_args"] == prompt.TOOL_ARGS + ["--resume", sid]
    # The CLI already holds the rules and the history; only the fresh snapshot
    # and the new question go over.
    assert seen["system"] == ""
    assert "그럼 피엔티는?" in seen["user"]


def test_a_failed_first_turn_still_sends_the_rules(client, monkeypatch):
    """A dead first turn can leave a session file with no rules in it. Resuming
    that one must not strip the model's tools and format."""
    monkeypatch.setattr(context, "account_context", lambda aid: "")
    monkeypatch.setattr(context, "news_context", lambda: "")
    monkeypatch.setattr(session, "cli_session_exists", lambda s: True)
    seen = {}
    _stub_backend(monkeypatch, seen=seen)

    sid = _new_thread(client)
    r = client.post("/api/accounts/alice/chat", cookies=_cookie(),
                    json={"session_id": sid, "message": "다시 물어볼게"})
    _wait(client, "alice", r.json()["job_id"])

    assert seen["extra_args"] == prompt.TOOL_ARGS + ["--resume", sid]
    assert seen["system"] == prompt.SYSTEM


def test_a_thread_whose_cli_memory_was_cleared_carries_on(client, monkeypatch):
    """Session files are deleted every midnight. The next question in an old
    thread starts a new CLI session with the rules and the thread's own saved
    messages, so the conversation continues instead of starting blind."""
    monkeypatch.setattr(context, "account_context", lambda aid: "[계좌]")
    monkeypatch.setattr(context, "news_context", lambda: "")
    seen = {}
    _stub_backend(monkeypatch, reply="삼성전자는 보유 비중이 큽니다.", seen=seen)

    sid = _new_thread(client)
    r = client.post("/api/accounts/alice/chat", cookies=_cookie(),
                    json={"session_id": sid, "message": "제일 큰 종목은?"})
    _wait(client, "alice", r.json()["job_id"])

    # midnight: the CLI transcript is gone, the thread on screen is not
    monkeypatch.setattr(session, "cli_session_exists", lambda s: False)
    r = client.post("/api/accounts/alice/chat", cookies=_cookie(),
                    json={"session_id": sid, "message": "그거 팔까?"})
    _wait(client, "alice", r.json()["job_id"])

    assert seen["extra_args"] == prompt.TOOL_ARGS + ["--session-id", sid]
    assert seen["system"] == prompt.SYSTEM
    assert "제일 큰 종목은?" in seen["user"] and "삼성전자는 보유 비중이 큽니다." in seen["user"]
    assert seen["user"].index("[이전 대화]") < seen["user"].index("그거 팔까?")


def test_backend_failure_surfaces_as_error(client, monkeypatch):
    monkeypatch.setattr(context, "account_context", lambda aid: "")
    monkeypatch.setattr(context, "news_context", lambda: "")

    class _Boom:
        def __init__(self, *a, **kw):
            pass

        def call(self, *a, **kw):
            raise RuntimeError("claude CLI exit 1: nope")

    monkeypatch.setattr(session, "ClaudeCliBackend", _Boom)
    sid = _new_thread(client)
    r = client.post("/api/accounts/alice/chat", cookies=_cookie(),
                    json={"session_id": sid, "message": "안녕"})
    d = _wait(client, "alice", r.json()["job_id"])
    assert d["state"] == "error" and "nope" in d["detail"]


def test_empty_message_short_circuits(client, monkeypatch):
    monkeypatch.setattr(session, "ClaudeCliBackend",
                        lambda *a, **kw: pytest.fail("must not spend a call"))
    sid = _new_thread(client)
    r = client.post("/api/accounts/alice/chat", cookies=_cookie(),
                    json={"session_id": sid, "message": "   "})
    assert r.json()["ok"] is False


def test_one_run_per_thread(client, monkeypatch):
    monkeypatch.setattr(context, "account_context", lambda aid: "")
    monkeypatch.setattr(context, "news_context", lambda: "")
    release = {"go": False}

    class _Slow:
        def __init__(self, *a, **kw):
            pass

        def call(self, *a, **kw):
            while not release["go"]:
                time.sleep(0.01)
            return "ok"

    monkeypatch.setattr(session, "ClaudeCliBackend", _Slow)
    sid = _new_thread(client)
    first = client.post("/api/accounts/alice/chat", cookies=_cookie(),
                        json={"session_id": sid, "message": "하나"})
    second = client.post("/api/accounts/alice/chat", cookies=_cookie(),
                         json={"session_id": sid, "message": "둘"})
    assert first.json()["ok"] is True
    assert second.json()["ok"] is False
    release["go"] = True
    _wait(client, "alice", first.json()["job_id"])


# ── thread CRUD ───────────────────────────────────────────────────────

def test_threads_list_and_delete(client, monkeypatch):
    a, b = _new_thread(client), _new_thread(client)
    ids = [c["session_id"] for c in
           client.get("/api/accounts/alice/chats", cookies=_cookie()).json()["chats"]]
    assert set(ids) == {a, b}

    removed = []
    monkeypatch.setattr(chat.chat_store.glob, "glob",
                        lambda pat: ["/fake/" + a + ".jsonl"])
    monkeypatch.setattr(chat.chat_store.os, "remove", removed.append)

    assert client.delete(f"/api/accounts/alice/chats/{a}",
                         cookies=_cookie()).status_code == 200
    assert removed == ["/fake/" + a + ".jsonl"], "CLI session file must go too"
    assert client.delete(f"/api/accounts/alice/chats/{a}",
                         cookies=_cookie()).status_code == 404
    ids = [c["session_id"] for c in
           client.get("/api/accounts/alice/chats", cookies=_cookie()).json()["chats"]]
    assert ids == [b]
