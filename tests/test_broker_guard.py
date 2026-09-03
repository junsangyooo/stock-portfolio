"""The conftest guard: no test may reach the live Toss API or claude CLI."""
import pytest

from src.core.notify import discord
from src.quant.broker import toss
from src.research.llm_backend import ClaudeCliBackend


def test_broker_http_is_blocked(monkeypatch):
    monkeypatch.setattr(toss, "token", lambda force=False: "tok")
    with pytest.raises(RuntimeError, match="test reached the live broker: GET"):
        toss.call("GET", "/x", group="MARKET_DATA")


def test_token_issue_is_blocked(monkeypatch):
    monkeypatch.setattr(toss, "creds", lambda: ("id", "secret"))
    with pytest.raises(RuntimeError, match="test reached the live broker: POST"):
        toss.token(force=True)


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
