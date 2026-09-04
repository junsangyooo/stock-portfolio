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
                           ``LOCAL_URL``

No timeouts by default. A failed ``claude -p`` exits 1 with a message (Oracle
logs: 167 auth errors, 2 overload errors — all surfaced that way); the only
timeouts ever logged (153) were the old 480s ceiling discarding generations
that were still running. A process that never returns has not been observed.
"""
from __future__ import annotations

import glob
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request


FAST_MODEL = "claude-haiku-4-5-20251001"

# On-host llama.cpp server (docker, 127.0.0.1 only — never exposed).
LOCAL_URL = os.getenv("LOCAL_LLM_URL", "http://127.0.0.1:8080")


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

# Credential-shaped env vars never passed to claude / its tool children.
_SENSITIVE_ENV = re.compile(
    r"(_KEY|_TOKEN|_SECRET|_PW|_PASSWORD|_WEBHOOK_URL)$|^(KIS_|KRX_)")


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

    def call(self, system: str, user: str, model: str | None = None,
             extra_args: list[str] | None = None) -> str:
        # extra_args: appended CLI flags, e.g. --allowedTools for the chat
        # agent's read-only lookup tool. Default None = plain text call.
        prompt = system + "\n\n---\n\n" + user
        m = model or self.model
        env = os.environ.copy()
        env.pop("ANTHROPIC_API_KEY", None)  # force subscription billing
        # Defense-in-depth: tool-enabled calls (chat --allowedTools) spawn a
        # Bash child that inherits this env, so strip anything credential-
        # shaped. Text-only agent calls never need these either.
        for k in [k for k in env if _SENSITIVE_ENV.search(k)]:
            env.pop(k, None)
        # --safe-mode: no project instruction files / hooks / plugins / MCP in the agent
        # context. Without it every call from the repo root ingested the
        # 500-line ops document (verified 2026-08-27: the agent quoted
        # §7 verbatim) — token waste plus dev-instruction contamination.
        # Verified compatible: subscription auth, --allowedTools, and
        # --resume --fork-session prompt-cache inheritance all work under
        # it. (--bare is stronger but never reads OAuth → breaks §7
        # subscription-only billing, so it is not an option.)
        r = subprocess.run(
            [self.bin, "-p", "--safe-mode",
             "--output-format", "text", "--model", m]
            + (extra_args or []),
            input=prompt, capture_output=True, text=True, env=env,
        )
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

    No timeout: the box is a 4-core Ampere with no GPU, so a full 40-item
    digest measured ~14 min (prefill 25 tok/s, generation down to ~2 tok/s
    once the context is long). Nothing here is interactive — the caller is a
    nightly batch — so slow is fine, silent is not: every failure raises so
    the caller can fall back to Claude.
    """

    name = "local_llama"

    def __init__(self, url: str = LOCAL_URL, model: str = "local"):
        self.url = url.rstrip("/")
        self.model = model

    def call(self, system: str, user: str, model: str | None = None) -> str:
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
            with urllib.request.urlopen(req) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            # HTTPError subclasses URLError — split it out, or a generation-side
            # 500 ("output does not match the expected format") gets logged as
            # "unreachable" and sends the next debugger after the network.
            raise RuntimeError(
                f"local llama HTTP {e.code} from {self.url}: "
                f"{e.read(300).decode('utf-8', 'replace')}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"local llama unreachable at {self.url}: {e}")
        try:
            content = data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, AttributeError):
            raise RuntimeError(f"local llama bad response: {str(data)[:200]}")
        # U+FFFD means the model emitted a byte sequence that is not valid
        # UTF-8 — a quantised 4B writing Korean does this occasionally. It is
        # never legitimate here (no collected signal or digest on disk has ever
        # contained one), and it corrupts silently: `"nov<FFFD>lty": true` still
        # parses as JSON, it just drops the field. Treat it as a failed
        # generation so the caller retries instead of storing bad data.
        if "\ufffd" in content:
            raise RuntimeError(
                "local llama produced invalid UTF-8 (U+FFFD) — corrupt generation")
        return content


_log = logging.getLogger("llm")


def _get_log() -> logging.Logger:
    """stderr logger with timestamps — cron redirects it into the job log.

    A bare ``print`` left fallbacks undatable: the news log is a timestamp-free
    append, so a past fallback could only be dated by its position relative to
    the next JSON summary block.
    """
    if not _log.handlers:
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s llm %(message)s"))
        _log.addHandler(h)
        _log.setLevel(logging.INFO)
        _log.propagate = False
    return _log


def local_or_claude(system: str, user: str, *, claude_model: str,
                    stage: str = "?", validate=None) -> tuple[str, str]:
    """Run on the local model, fall back to Claude on any failure.

    Returns ``(raw_text, backend_name)`` so callers can log which one served.
    Used by the mechanical news stages; judgment stages call Claude directly.

    ``stage`` labels the caller (e.g. ``"digest:battery"``) — it is the only
    record of WHICH theme fell back for the state stage, which drops the
    returned backend name.

    The local call gets one retry first: the only fallback observed so far was
    a llama.cpp 500 on a generation its chat parser rejected — a dice roll at
    temperature 0.2, not a dead server.

    ``validate`` (e.g. ``_parse_json``) runs on the local answer and must raise
    to reject it. Without it, unusable local output escapes this function and
    blows up in the caller — where there is no retry and no Claude to fall back
    to, so the theme is simply lost for the day.
    """
    for attempt in (1, 2):
        try:
            out = LocalLlamaBackend().call(system, user)
            if validate is not None:
                validate(out)
            return out, "local_llama"
        except Exception as e:  # noqa: BLE001 — any local fault must not lose the run
            retrying = attempt == 1
            _get_log().warning(
                f"[{stage}] local backend failed (attempt {attempt}: {str(e)[:200]}) "
                f"— {'retrying locally' if retrying else f'falling back to Claude ({claude_model})'}")
            if not retrying:
                break
    out = ClaudeCliBackend(model=claude_model).call(system, user, model=claude_model)
    _get_log().warning(f"[{stage}] served by claude_cli ({claude_model})")
    return out, "claude_cli"


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
