"""One session holds the dictionaries; each symbol forks it.

Measured 2026-09-15: `claude -p` caches a user message as one block, so a
message that differs anywhere shares nothing. The saving can only come from
the session boundary — which is what these pin.
"""
from __future__ import annotations

import json

import pytest

from src.research.analyze import engine, inputs, prompt
from tests.test_analyze import _OK, _STRUCT, _envelope


class _Backend:
    """Records what each call was given."""

    def __init__(self, fail_first: bool = False):
        self.calls: list[dict] = []
        self.fail_first = fail_first

    def call(self, system, user, model=None, extra_args=None):
        self.calls.append({"system": system, "user": user,
                           "extra": extra_args or []})
        if self.fail_first and len(self.calls) == 1:
            raise RuntimeError("fork failed")
        return json.dumps(_OK)


@pytest.fixture
def assembled(monkeypatch):
    env = _envelope(_STRUCT)
    monkeypatch.setattr(engine.inputs, "build", lambda *a, **kw: env)
    return env


@pytest.fixture(autouse=True)
def _cold_root(monkeypatch):
    monkeypatch.setattr(engine, "_root", None)


# ── the two halves ──────────────────────────────────────────────────────────

def test_the_root_carries_every_dictionary_and_no_symbol():
    """It has to be byte-identical for every symbol in the batch, so it keeps
    all three dictionaries even when a symbol is missing a domain."""
    text = prompt.root_context([
        {"id": d, "title_ko": d, "reference": f"# {d} 사전"}
        for d in ("chart", "fundamental", "news")])
    for d in ("chart", "fundamental", "news"):
        assert f"# {d} 사전" in text
    assert '"observations"' in text            # the contract rides along
    assert "삼성전자" not in text
    assert "준비됨" in text                     # and asks for nothing else


def test_a_fork_sends_the_symbol_and_not_the_dictionaries():
    text = prompt.symbol_part(_envelope(_STRUCT))
    assert "삼성전자" in text and "데이터: chart" in text
    assert "# chart 사전" not in text
    assert "JSON 한 덩이" in text                # still told what to answer


def test_the_single_call_prompt_still_has_both_halves():
    text = prompt.build(_envelope(_STRUCT))
    assert "# chart 사전" in text and "삼성전자" in text


# ── the call ────────────────────────────────────────────────────────────────

def test_with_a_root_the_call_forks_it_and_sends_only_the_symbol(assembled):
    be = _Backend()
    out = engine.analyze("KR:005930", "삼성전자", backend=be, store=False,
                         root="sess-1")
    assert out["status"] == "ok" and len(be.calls) == 1
    call = be.calls[0]
    assert call["extra"] == ["--resume", "sess-1", "--fork-session"]
    assert call["system"] == ""                 # the contract is in the session
    assert "# chart 사전" not in call["user"]


def test_a_failed_fork_falls_back_to_the_whole_prompt(assembled):
    """If the fork fails the session is the first thing to suspect, so the
    retry must not depend on it — a symbol is not lost to a cold root."""
    be = _Backend(fail_first=True)
    out = engine.analyze("KR:005930", "삼성전자", backend=be, store=False,
                         root="sess-1")
    assert out["status"] == "ok" and len(be.calls) == 2
    assert be.calls[1]["extra"] == []
    assert be.calls[1]["system"] == prompt.SYSTEM
    assert "# chart 사전" in be.calls[1]["user"]


def test_without_a_root_nothing_changes(assembled):
    be = _Backend()
    engine.analyze("KR:005930", "삼성전자", backend=be, store=False)
    assert be.calls[0]["extra"] == []
    assert "# chart 사전" in be.calls[0]["user"]


# ── the root's lifetime ─────────────────────────────────────────────────────

def test_one_root_serves_the_batch_until_it_is_old(monkeypatch):
    be = _Backend()
    monkeypatch.setattr(inputs, "references", lambda: [
        {"id": "chart", "title_ko": "차트", "reference": "# chart 사전"}])
    clock = [1000.0]
    monkeypatch.setattr(engine.time, "monotonic", lambda: clock[0])

    first = engine.batch_root(backend=be)
    assert first and len(be.calls) == 1
    assert be.calls[0]["extra"][:1] == ["--session-id"]

    clock[0] += engine._ROOT_TTL_S - 1          # still warm
    assert engine.batch_root(backend=be) == first and len(be.calls) == 1

    clock[0] += 2                               # past the refresh
    second = engine.batch_root(backend=be)
    assert second != first and len(be.calls) == 2


def test_a_dead_root_is_not_fatal(monkeypatch):
    """None means "send the whole prompt" — what every call did before."""
    class _Boom:
        def call(self, *a, **kw):
            raise RuntimeError("claude CLI exit 1")

    monkeypatch.setattr(inputs, "references", lambda: [])
    assert engine.batch_root(backend=_Boom()) is None
