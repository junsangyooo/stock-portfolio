"""LLM backend — Claude CLI + local open-weight invocation for the agent stack.

NO PAID API policy (user global rule). Judgment calls go through the local
`claude -p` subprocess so they bill against the Claude subscription and
never against API credits. Mechanical work (news digest/state) runs on an
open-weight model served by llama.cpp on this host — free, and it keeps the
subscription budget for the layers that actually decide something.

Contents:
  - ``ClaudeCliBackend``   subprocess wrapper around `claude -p`
  - ``LocalLlamaBackend``  HTTP client for the on-host llama.cpp server
  - ``_parse_json``        lenient JSON extractor — FIRST object, raises
  - constants              ``FAST_MODEL`` (Haiku 5.5), ``SLOW_MODEL`` (Sonnet 5.5),
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
import time
import urllib.error
import urllib.request

from . import usage


FAST_MODEL = "claude-haiku-5-5"

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
SLOW_MODEL = "claude-sonnet-5-5"
# The chat agent: Opus 5.5 at medium effort (his choice, 2026-10-15) — it reads
# with tools over several turns and answers a person who is waiting.
CHAT_MODEL = "claude-opus-5-5"

# Reasoning budget per task (2026-10-15, his choice). Thinking is not saved;
# a summary or a label does not get better with more of it.
#   news:enrich · daily fallback · news:mapping   low
#   analyze (18:00 and the 다시 분석 button)       low
#   chat                                          medium (Opus)
EFFORT_LOW, EFFORT_MEDIUM = "low", "medium"

# Credential-shaped env vars never passed to claude / its tool children.
_SENSITIVE_ENV = re.compile(
    r"(_KEY|_TOKEN|_SECRET|_PW|_PASSWORD|_WEBHOOK_URL)$|^(KIS_|KRX_)")


# The CLI's total_cost_usd is the whole session's, not the call's: a fork of
# the analysis root carried the root's $0.12 again (five forks, measured
# 2026-10-15) and a resumed chat turn carries every earlier turn. The tokens are
# the call's own; only the dollar figure needs the session's previous total
# taken off. Kept in memory: after a restart the first resumed turn of an old
# chat is counted whole once.
_session_cost: dict[str, float] = {}


def _own_cost(args: list[str], total) -> float | None:
    if total is None:
        return None
    try:
        total = float(total)
    except (TypeError, ValueError):
        return None

    def flag(name):
        return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else None

    root, parent = flag("--session-id"), flag("--resume")
    if root:                                   # a new session: all of it is this call
        _session_cost[root] = total
        return total
    if parent:
        own = max(0.0, total - _session_cost.get(parent, 0.0))
        if "--fork-session" not in args:       # the same session goes on
            _session_cost[parent] = total
        return own
    return total


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

    def __init__(self, model: str = SLOW_MODEL, stage: str = "?",
                 effort: str | None = None):
        self.model = model
        self.stage = stage      # what the usage ledger calls this call site
        self.effort = effort    # --effort for every call, unless the call names one
        self.bin = _find_claude()
        if not self.bin:
            raise RuntimeError("`claude` CLI not found (PATH or nvm/usr/local)")

    def call(self, system: str, user: str, model: str | None = None,
             extra_args: list[str] | None = None, cwd: str | None = None,
             env_extra: dict | None = None) -> str:
        # extra_args: appended CLI flags, e.g. `--tools Bash --allowedTools …`
        # for the chat agent's lookup command. Default None = no tools at all.
        # cwd: where the CLI runs. A tool-enabled call must run in an empty
        # directory — see the tool lockdown below.
        # env_extra: set after the scrub, for the tool child to read.
        prompt = system + "\n\n---\n\n" + user
        m = model or self.model
        env = os.environ.copy()
        env.pop("ANTHROPIC_API_KEY", None)  # force subscription billing
        # Defense-in-depth: tool-enabled calls (chat --allowedTools) spawn a
        # Bash child that inherits this env, so strip anything credential-
        # shaped. Text-only agent calls never need these either.
        for k in [k for k in env if _SENSITIVE_ENV.search(k)]:
            env.pop(k, None)
        env.update(env_extra or {})
        # --safe-mode: no project instruction files / hooks / plugins / MCP in the agent
        # context. Without it every call from the repo root ingested the
        # 500-line ops document (verified 2026-08-27: the agent quoted
        # §7 verbatim) — token waste plus dev-instruction contamination.
        # Verified compatible: subscription auth, --allowedTools, and
        # --resume --fork-session prompt-cache inheritance all work under
        # it. (--bare is stronger but never reads OAuth → breaks §7
        # subscription-only billing, so it is not an option.)
        args = list(extra_args or [])
        # Tool lockdown (measured 2026-10-13, CLI 2.1.296). Under --safe-mode
        # alone a chat agent read ops notes through the Read tool and through
        # `head`, and the server's own ~/.claude/settings.local.json allow
        # rules (`sudo rm *`, `crontab *`) still applied — safe mode keeps
        # permissions. --restricted drops user/project/local settings, and
        # dontAsk denies whatever no allow rule names. Neither stops the CLI's
        # built-in read-only commands (head, cat, ls), which it allows inside
        # the working directory — only an empty cwd confines those
        # (`chat_cwd`). A call that asks for no tools gets none.
        effort = getattr(self, "effort", None)   # tests build bare instances
        if effort and "--effort" not in args:
            args += ["--effort", effort]
        args = ["--restricted", "--permission-mode", "dontAsk"] + args
        if "--tools" not in args:
            args += ["--tools", ""]
        # Only a session somebody resumes is written to disk: a chat thread
        # (--resume, extended turn by turn) or an analysis root that forks
        # read (--session-id). A one-shot call and a fork never are, and each used
        # to leave a transcript behind (~100 a day on the server).
        if "--session-id" not in args and ("--resume" not in args
                                           or "--fork-session" in args):
            args.append("--no-session-persistence")
        t0 = time.monotonic()
        # json, not text: the envelope carries this call's own token counts,
        # and they are the only place per-call usage exists. The answer is
        # the `result` field, so callers see no difference.
        r = subprocess.run(
            [self.bin, "-p", "--safe-mode",
             "--output-format", "json", "--model", m] + args,
            input=prompt, capture_output=True, text=True, env=env, cwd=cwd,
        )
        elapsed = time.monotonic() - t0
        if r.returncode != 0:
            # Surface stdout too — CLI often emits errors there (e.g.,
            # "Credit balance is too low" was on stdout, leaving stderr empty).
            err_blob = (r.stderr or r.stdout or "").strip()[:200]
            # 401 / expired subscription token → tag as AUTH so log scans and
            # alerting can distinguish "needs `claude login` on the host" from
            # transient failures (17-day silent outage of 2026-06/07).
            usage.record(self.stage, m, ok=False, error=err_blob,
                         elapsed_s=elapsed)
            if "401" in err_blob or "authenticate" in err_blob.lower():
                raise RuntimeError(
                    f"claude CLI AUTH error (re-login needed): {err_blob}")
            raise RuntimeError(f"claude CLI exit {r.returncode}: {err_blob}")
        text = r.stdout.strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict) and "result" in data:
            # `is_error` is recorded, not raised on. The CLI exits 0 and still
            # hands back whatever it produced; discarding that would lose a
            # generation over a label, and every caller already validates the
            # shape it needs. `stop_reason` is the honest answer to "was this
            # cut off at the output ceiling" — the plan pass has to guess.
            usage.record(self.stage, m, usage=data.get("usage"),
                         cost_usd=_own_cost(args, data.get("total_cost_usd")),
                         elapsed_s=elapsed, ok=not data.get("is_error"),
                         error=data.get("subtype") if data.get("is_error") else None,
                         extra={"turns": data.get("num_turns"),
                                "stop_reason": data.get("stop_reason")})
            return str(data.get("result") or "").strip()
        # An envelope we cannot read is not a lost generation — the answer is
        # still on stdout. Only the accounting for that call is lost.
        usage.record(self.stage, m, elapsed_s=elapsed,
                     extra={"envelope": "unreadable"})
        return text


class LocalLlamaBackend:
    """OpenAI-compatible client for the llama.cpp server running on this host.

    No timeout: the box is a 4-core Ampere with no GPU, so a full 40-item
    digest measured ~14 min (prefill 25 tok/s, generation down to ~2 tok/s
    once the context is long). Nothing here is interactive — the caller is a
    nightly batch — so slow is fine, silent is not: every failure raises so
    the caller can fall back to Claude.
    """

    name = "local_llama"

    def __init__(self, url: str = LOCAL_URL, model: str = "local",
                 max_tokens: int = 1200, stage: str = "?"):
        self.url = url.rstrip("/")
        self.model = model
        self.max_tokens = max_tokens
        self.stage = stage

    def call(self, system: str, user: str, model: str | None = None) -> str:
        body = json.dumps({
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": 0.2,
            "max_tokens": self.max_tokens,
        }).encode()
        req = urllib.request.Request(
            self.url + "/v1/chat/completions", data=body,
            headers={"Content-Type": "application/json"})
        t0 = time.monotonic()

        def _fail(msg: str):
            """Record, then raise. A dead local model shows up in the ledger
            as the row before a `:fallback` — otherwise the subscription
            spend appears out of nowhere and the box looks innocent."""
            usage.record(self.stage, self.model, backend="local_llama",
                         ok=False, error=msg,
                         elapsed_s=time.monotonic() - t0)
            raise RuntimeError(msg)

        try:
            with urllib.request.urlopen(req) as r:
                data = json.load(r)
        except urllib.error.HTTPError as e:
            # HTTPError subclasses URLError — split it out, or a generation-side
            # 500 ("output does not match the expected format") gets logged as
            # "unreachable" and sends the next debugger after the network.
            _fail(f"local llama HTTP {e.code} from {self.url}: "
                  f"{e.read(300).decode('utf-8', 'replace')}")
        except urllib.error.URLError as e:
            _fail(f"local llama unreachable at {self.url}: {e}")
        try:
            content = data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, AttributeError):
            _fail(f"local llama bad response: {str(data)[:200]}")
        # U+FFFD means the model emitted a byte sequence that is not valid
        # UTF-8 — a quantised 4B writing Korean does this occasionally. It is
        # never legitimate here (no collected signal or digest on disk has ever
        # contained one), and it corrupts silently: `"nov<FFFD>lty": true` still
        # parses as JSON, it just drops the field. Treat it as a failed
        # generation so the caller retries instead of storing bad data.
        if "\ufffd" in content:
            _fail("local llama produced invalid UTF-8 (U+FFFD) — corrupt generation")
        # Free, but not weightless: this is the hour the box spent.
        usage.record(self.stage, self.model, backend="local_llama",
                     usage=data.get("usage"), cost_usd=0.0,
                     elapsed_s=time.monotonic() - t0)
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
    # The ledger groups by stage, so the key has to be the stage and not the
    # item: callers label per theme ("daily:semiconductor") for the logs,
    # which would otherwise put 69 rows in a report meant to be read.
    ledger = stage.split(":")[0]
    for attempt in (1, 2):
        try:
            out = LocalLlamaBackend(stage=ledger).call(system, user)
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
    out = ClaudeCliBackend(model=claude_model, effort=EFFORT_LOW,
                           stage=f"{ledger}:fallback").call(
                               system, user, model=claude_model)
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
