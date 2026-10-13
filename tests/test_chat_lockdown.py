"""What a `claude -p` call may touch.

Measured 2026-10-13: under --safe-mode alone the chat agent read ops notes
through the Read tool and through `head`, and the server's own Claude settings
allowed `sudo rm *` and `crontab *` to any call. These pin the lockdown: every
call drops the settings files and denies what no rule names, a call that asks
for no tools gets none, and the chat agent gets Bash, one command, and an
empty working directory.
"""
from __future__ import annotations

import json

from src.api import chat
from src.core import paths
from src.research import llm_backend

# Captured at import, before conftest replaces it.
_REAL_CALL = llm_backend.ClaudeCliBackend.call

_ENVELOPE = json.dumps({"result": "ok", "usage": {}})


def _run_capture(monkeypatch):
    seen: dict = {}

    def _run(argv, **kw):
        seen.update(argv=argv, **kw)
        return type("R", (), {"returncode": 0, "stdout": _ENVELOPE,
                              "stderr": ""})()
    monkeypatch.setattr(llm_backend.subprocess, "run", _run)
    b = llm_backend.ClaudeCliBackend.__new__(llm_backend.ClaudeCliBackend)
    b.bin, b.model, b.stage = "claude", "sonnet", "test"
    return b, seen


def _flag(argv, name):
    return argv[argv.index(name) + 1]


def test_a_call_without_tools_gets_none(monkeypatch):
    """Analysis, news enrich and tagging ask for no tools — the CLI's default
    set (Read, Grep, Bash…) must not be there."""
    b, seen = _run_capture(monkeypatch)
    _REAL_CALL(b, "sys", "user")
    argv = seen["argv"]
    assert _flag(argv, "--tools") == ""
    assert "--restricted" in argv
    assert _flag(argv, "--permission-mode") == "dontAsk"


def test_a_fork_is_locked_down_like_its_root(monkeypatch):
    """Root and fork carry the same flags, so the cached prefix still matches."""
    b, seen = _run_capture(monkeypatch)
    _REAL_CALL(b, "sys", "user", extra_args=["--session-id", "S"])
    root = list(seen["argv"])
    _REAL_CALL(b, "sys", "user", extra_args=["--resume", "S", "--fork-session"])
    fork = seen["argv"]
    for f in ("--restricted", "--tools", "--permission-mode"):
        assert f in root and f in fork
    assert _flag(fork, "--tools") == _flag(root, "--tools") == ""


def test_a_caller_naming_tools_keeps_its_own(monkeypatch):
    b, seen = _run_capture(monkeypatch)
    _REAL_CALL(b, "sys", "user", extra_args=["--tools", "Bash"])
    argv = seen["argv"]
    assert argv.count("--tools") == 1 and _flag(argv, "--tools") == "Bash"
    assert "--restricted" in argv


def test_env_extra_survives_the_credential_scrub(monkeypatch):
    monkeypatch.setenv("DART_API_KEY", "secret")
    b, seen = _run_capture(monkeypatch)
    _REAL_CALL(b, "sys", "user", cwd="/x", env_extra={"PYTHONPATH": "/repo"})
    assert seen["cwd"] == "/x"
    assert seen["env"]["PYTHONPATH"] == "/repo"
    assert "DART_API_KEY" not in seen["env"]


def test_chat_gets_bash_and_one_command():
    argv = chat.TOOL_ARGS
    assert _flag(argv, "--tools") == "Bash"
    allowed = _flag(argv, "--allowedTools")
    assert allowed == f"Bash({chat.TOOL_CMD}:*)"
    assert argv.count("--allowedTools") == 1
    # Absolute: the agent runs outside the repo and cannot reach it otherwise.
    assert chat.TOOL_CMD.startswith(str(paths.ROOT))
    # The prompt names the same command the rule allows.
    assert "$TOOL" not in chat.SYSTEM and chat.TOOL_CMD in chat.SYSTEM


def test_chat_runs_in_the_empty_sandbox(monkeypatch, tmp_path):
    """The CLI allows its read-only commands anywhere under the cwd, so the
    cwd must hold nothing — not the repo, not data/."""
    seen: dict = {}

    class _Stub:
        def __init__(self, *a, **kw):
            pass

        def call(self, system, user, model=None, extra_args=None,
                 cwd=None, env_extra=None):
            seen.update(cwd=cwd, env_extra=env_extra, extra_args=extra_args)
            return "답"

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.core import security
    from src.portfolio.store import account_store, chat_store

    monkeypatch.setattr(chat, "ClaudeCliBackend", _Stub)
    monkeypatch.setattr(chat, "_jobs", {})
    account_store.create("alice", "alice", "GENERAL")
    account_store.set_pin_hash("alice", security.hash_pin("alice", "123456"))
    sid = chat_store.create("alice", "t")
    app = FastAPI()
    app.include_router(chat.router)
    c = TestClient(app)
    c.cookies.set(security.COOKIE_PREFIX + "alice",
                  security.make_session_token("alice"))
    job = c.post("/api/accounts/alice/chat",
                 json={"session_id": sid, "message": "안녕"}).json()["job_id"]
    for _ in range(100):
        if c.get(f"/api/accounts/alice/chat/status?job_id={job}").json()["state"] != "running":
            break
        import time
        time.sleep(0.02)

    assert seen["cwd"] == str(paths.CHAT_CWD)
    assert paths.CHAT_CWD.is_dir() and not any(paths.CHAT_CWD.iterdir())
    assert seen["env_extra"]["PYTHONPATH"] == str(paths.ROOT)
    assert seen["extra_args"][:len(chat.TOOL_ARGS)] == chat.TOOL_ARGS


def test_session_lookup_reads_only_the_sandbox_project(monkeypatch, tmp_path):
    """--resume finds a session by the cwd's project, so a transcript under
    the repo's project (a turn from before the cwd moved) is not resumable.
    The project name is the server path as the CLI spells it."""
    from pathlib import Path
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(paths, "CHAT_CWD",
                        Path("/home/ubuntu/portfolio-dashboard/data/chat_sandbox"))
    proj = tmp_path / ".claude" / "projects"
    for name, sid in (("-home-ubuntu-portfolio-dashboard", "old"),
                      ("-home-ubuntu-portfolio-dashboard-data-chat-sandbox", "new")):
        (proj / name).mkdir(parents=True)
        (proj / name / f"{sid}.jsonl").write_text("")
    assert chat._cli_session_exists("new")
    assert not chat._cli_session_exists("old")
