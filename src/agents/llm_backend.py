"""LLM backend — Claude CLI invocation for the agent stack.

CLI ONLY policy (user global rule). The Anthropic API path is intentionally
removed; we rely exclusively on the local `claude -p` subprocess so all calls
bill against the Claude subscription and never against API credits.

Contents:
  - ``_Backend``           ABC for swappable backends
  - ``ClaudeCliBackend``   subprocess wrapper around `claude -p`
  - ``_parse_json``        lenient JSON extractor (handles fenced blocks)
  - constants              ``FAST_MODEL`` (Haiku 4.5), ``SLOW_MODEL`` (Sonnet 4.6)

Migrated from an earlier trading module during the 2026-05
cleanup. Its ``Decision`` dataclass,
``decide()`` orchestrator, ``ApiBackend`` (API path), and ``_pick_backend``
selector were dropped.
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
from abc import ABC, abstractmethod


FAST_MODEL = "claude-haiku-4-5-20251001"


def _find_claude() -> str | None:
    """Locate the `claude` binary. PATH first, then common install dirs.

    cron / systemd run with a minimal PATH that misses nvm's bin, so we fall
    back to the usual locations (nvm node, /usr/local, homebrew).
    """
    p = shutil.which("claude")
    if p:
        return p
    patterns = [
        os.path.expanduser("~/.nvm/versions/node/*/bin/claude"),
        "/usr/local/bin/claude",
        "/opt/homebrew/bin/claude",
        os.path.expanduser("~/.local/bin/claude"),
    ]
    for pat in patterns:
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]
    return None
SLOW_MODEL = "claude-sonnet-4-6"


class _Backend(ABC):
    """ABC for LLM backends. Concrete backends accept a (system, user) prompt
    pair and return raw text. JSON parsing is the caller's responsibility
    (use ``_parse_json``)."""

    name = "base"

    @abstractmethod
    def call(self, system: str, user: str, timeout_s: float = 60,
             model: str | None = None) -> str:
        raise NotImplementedError


class ClaudeCliBackend(_Backend):
    """Subprocess wrapper around ``claude -p``.

    IMPORTANT — env scrubbing: ``ANTHROPIC_API_KEY`` is stripped from the
    subprocess env before exec. When present, the Claude CLI routes
    billing through the API account (per-token cost) instead of using the
    local subscription token in ``~/.claude.json``. We pick this backend
    specifically when subscription billing is desired (paper / dev),
    so leaking the key silently re-routes to API billing and breaks the
    moment credits run out (``Credit balance too low`` → exit 1).
    """

    name = "claude_cli"

    def __init__(self, model: str = SLOW_MODEL):
        self.model = model
        self.bin = _find_claude()
        if not self.bin:
            raise RuntimeError("`claude` CLI not found (PATH or nvm/usr/local)")

    def call(self, system: str, user: str, timeout_s: float = 60,
             model: str | None = None) -> str:
        prompt = system + "\n\n---\n\n" + user
        m = model or self.model
        env = os.environ.copy()
        env.pop("ANTHROPIC_API_KEY", None)  # force subscription billing
        try:
            r = subprocess.run(
                [self.bin, "-p", "--output-format", "text", "--model", m],
                input=prompt, capture_output=True, text=True,
                timeout=timeout_s, env=env,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"claude CLI timeout ({timeout_s}s)")
        if r.returncode != 0:
            # Surface stdout too — CLI often emits errors there (e.g.,
            # "Credit balance is too low" was on stdout, leaving stderr empty).
            err_blob = (r.stderr or r.stdout or "").strip()[:200]
            # 401 / expired subscription token → tag as AUTH so log scans and
            # alerting can distinguish "needs `claude login` on the host" from
            # transient failures (17-day silent outage of 2026-06/07).
            if "401" in err_blob or "authenticate" in err_blob.lower():
                raise RuntimeError(
                    f"claude CLI AUTH error (re-login needed): {err_blob}")
            raise RuntimeError(f"claude CLI exit {r.returncode}: {err_blob}")
        return r.stdout.strip()


def _parse_json(text: str) -> dict:
    """Extract the first top-level JSON object from `text`.

    Tolerates:
      - markdown fenced code blocks (``` or ```json)
      - preamble before the opening brace (model scratchpad)
      - trailing content after the closing brace

    Raises ``ValueError`` if no balanced ``{...}`` is found.
    """
    s = text.strip()
    # strip markdown fences if present
    if s.startswith("```"):
        lines = s.split("\n")
        lines = lines[1:]  # drop opening ```
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    # find first { ... } block
    if not s.startswith("{"):
        i = s.find("{")
        if i >= 0:
            s = s[i:]
    # walk braces to find the matching close
    depth = 0
    end = -1
    for i, c in enumerate(s):
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end < 0:
        raise ValueError("no JSON object found in:\n" + text[:500])
    return json.loads(s[: end + 1])
