"""LLM backend — Claude CLI + local open-weight invocation for the agent stack.

NO PAID API policy (user global rule). Judgment calls go through the local
`claude -p` subprocess so they bill against the Claude subscription and
never against API credits. Mechanical work (news digest/state) runs on an
open-weight model served by llama.cpp on this host — free, and it keeps the
subscription budget for the layers that actually decide something.

Contents:
  - ``ClaudeCliBackend``   subprocess wrapper around `claude -p`
  - ``LocalLlamaBackend``  HTTP client for the on-host llama.cpp server
  - ``_parse_json``        lenient JSON extractor (handles fenced blocks)
  - constants              ``FAST_MODEL`` (Haiku 4.5), ``SLOW_MODEL`` (Sonnet 4.6),
                           ``LOCAL_URL`` / ``LOCAL_TIMEOUT_S``

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
import sys
import urllib.error
import urllib.request


FAST_MODEL = "claude-haiku-4-5-20251001"

# On-host llama.cpp server (docker, 127.0.0.1 only — never exposed).
LOCAL_URL = os.getenv("LOCAL_LLM_URL", "http://127.0.0.1:8080")
LOCAL_TIMEOUT_S = 1800  # measured worst case ~14 min for a 40-item digest


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


class ClaudeCliBackend:
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


class LocalLlamaBackend:
    """OpenAI-compatible client for the llama.cpp server running on this host.

    Deliberately generous timeout: the box is a 4-core Ampere with no GPU, so
    a full 40-item digest measured ~14 min (prefill 25 tok/s, generation down
    to ~2 tok/s once the context is long). Nothing here is interactive — the
    caller is a nightly batch — so slow is fine, silent is not: every failure
    raises so the caller can fall back to Claude.
    """

    name = "local_llama"

    def __init__(self, url: str = LOCAL_URL, model: str = "local"):
        self.url = url.rstrip("/")
        self.model = model

    def call(self, system: str, user: str, timeout_s: float = LOCAL_TIMEOUT_S,
             model: str | None = None) -> str:
        body = json.dumps({
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": 0.2,
            "max_tokens": 1200,
        }).encode()
        req = urllib.request.Request(
            self.url + "/v1/chat/completions", data=body,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as r:
                data = json.load(r)
        except urllib.error.URLError as e:
            raise RuntimeError(f"local llama unreachable at {self.url}: {e}")
        except TimeoutError:
            raise RuntimeError(f"local llama timeout ({timeout_s}s)")
        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, AttributeError):
            raise RuntimeError(f"local llama bad response: {str(data)[:200]}")


def local_or_claude(system: str, user: str, *, claude_model: str,
                    timeout_s: float = LOCAL_TIMEOUT_S,
                    claude_timeout_s: float = 300) -> tuple[str, str]:
    """Run on the local model, fall back to Claude on any failure.

    Returns ``(raw_text, backend_name)`` so callers can log which one served.
    Used by the mechanical news stages; judgment stages call Claude directly.
    """
    try:
        return LocalLlamaBackend().call(system, user, timeout_s=timeout_s), "local_llama"
    except Exception as e:  # noqa: BLE001 — any local fault must not lose the run
        print(f"[llm] local backend failed ({str(e)[:150]}), falling back to Claude",
              file=sys.stderr)
    return (ClaudeCliBackend(model=claude_model).call(
        system, user, timeout_s=claude_timeout_s, model=claude_model), "claude_cli")


def _parse_json(text: str) -> dict:
    """Extract the first top-level JSON object from `text`.

    Tolerates:
      - markdown fenced code blocks (``` or ```json)
      - preamble before the opening brace (model scratchpad)
      - trailing content after the closing brace

    Raises ``ValueError`` if no parseable object is found.
    """
    s = text.strip()
    # strip markdown fences if present
    if s.startswith("```"):
        lines = s.split("\n")[1:]          # drop opening ```
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    start = s.find("{")
    if start < 0:
        raise ValueError("no JSON object found in:\n" + text[:500])
    try:
        # raw_decode reads exactly one value and ignores whatever follows it,
        # so trailing prose after the object costs nothing.
        obj, _ = json.JSONDecoder().raw_decode(s[start:])
    except json.JSONDecodeError as e:
        raise ValueError(f"no JSON object found ({e}) in:\n{text[:500]}")
    return obj
