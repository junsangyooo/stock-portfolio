"""The conftest guard: no test may reach a live broker API, Discord or claude CLI."""
import socket

import pytest

from src.core.notify import discord
from src.market.source import kis_auth
from src.research.llm_backend import ClaudeCliBackend

_CREDS = {"app_key": "k", "app_secret": "s", "cano": "00000000", "prdt": "01",
          "real": True, "base": kis_auth.REAL_URL}


@pytest.fixture(autouse=True)
def _no_socket(monkeypatch):
    """Belt and braces: were the guard under test broken, these requests would
    fail here with another error instead of leaving the machine."""
    def _refuse(*a, **k):
        raise OSError("socket blocked in test_broker_guard")

    monkeypatch.setattr(socket.socket, "connect", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    monkeypatch.setattr(socket, "getaddrinfo", _refuse)


def test_broker_http_is_blocked(monkeypatch):
    monkeypatch.setattr(kis_auth, "creds", lambda: dict(_CREDS))
    monkeypatch.setattr(kis_auth, "token", lambda force=False: "tok")
    with pytest.raises(RuntimeError, match="test reached the live broker: GET"):
        kis_auth.call("/x", "TR")


def test_token_issue_is_blocked(monkeypatch):
    monkeypatch.setattr(kis_auth, "creds", lambda: dict(_CREDS))
    with pytest.raises(RuntimeError, match="test reached the live broker: POST"):
        kis_auth.token(force=True)
    assert not kis_auth._TOKEN_PATH.exists()


def test_the_token_cache_is_a_throwaway_file(tmp_path):
    assert kis_auth._TOKEN_PATH == tmp_path / ".kis_token.json"


def test_discord_is_unplugged(monkeypatch):
    posted = []
    monkeypatch.setattr(discord.httpx, "post",
                        lambda *a, **k: posted.append(a) or None)
    assert discord.send("x") is False
    assert discord.send_embed("t") is False
    assert posted == []


def test_claude_cli_is_blocked(monkeypatch):
    monkeypatch.setattr(ClaudeCliBackend, "__init__", lambda self, *a, **k: None)
    with pytest.raises(RuntimeError, match="test reached claude CLI"):
        ClaudeCliBackend().call("system", "user")
