"""One chat turn: pick the CLI session flags, run the agent, keep the answer.

Threads: a chat is a row in ``chat_sessions`` whose ``session_id`` is also the
Claude CLI session id. The first turn runs ``--session-id <uuid>``, every later
turn ``--resume <uuid>``, so the model keeps its own transcript — tool lookups
and their raw results included — and never re-fetches what it already pulled.
Our own copy of the messages exists only to repaint the browser.

Async (core/jobs.py): the route never waits for the model. The old
``--max-turns 8`` existed only to squeeze the agent loop under Cloudflare's
~100s; with the wait gone the loop is uncapped, and a heavy question (screen →
per-candidate fundamentals) takes as long as it needs.

Billing: ClaudeCliBackend (subscription), never the paid API. ops notes §7.
"""
from __future__ import annotations

import os
import re

from ...core import jobs, paths
from ...core.llm import CHAT_MODEL, EFFORT_MEDIUM, ClaudeCliBackend
from .. import security
from ..store import chat_store
from . import prompt

# Display rule, not a timeout: the CLI call itself is unbounded. Past this the
# poller reports 'timed out' and a new turn may start on the thread. Sized
# above the longest honest run (a screen plus a dozen per-symbol lookups);
# chat has no measured latency distribution yet.
RUNNER = jobs.Runner(stale_s=1800)


def cli_session_exists(session_id: str) -> bool:
    """Whether the CLI already has a transcript for this id.

    Ground truth for --session-id vs --resume: our message log can disagree
    (a first turn that died after the CLI wrote its file would otherwise be
    retried with --session-id and rejected as already taken).

    Only the chat cwd's project counts: --resume finds a session by the
    directory it runs in, so a transcript elsewhere (a turn from before the
    cwd moved) is one it cannot resume. The CLI names a project after its
    path with every non-alphanumeric character turned into '-'.
    """
    project = re.sub(r"[^A-Za-z0-9]", "-", str(paths.CHAT_CWD))
    return os.path.exists(os.path.expanduser(
        f"~/.claude/projects/{project}/{session_id}.jsonl"))


def ask(account_id: str, thread: dict, message: str) -> str:
    """Store the question, start the turn, return its job id.

    Two separate questions. Which flag: does the CLI already know this id?
    Whether to re-send the rules: has an answer actually landed? A first turn
    that died may have left a session behind without the rules in it, and
    resuming that one silently strips the model's tools and format. A third:
    the CLI transcripts are deleted every midnight, so an old thread can have
    answers on screen and no memory behind them. That turn starts a new
    session with the rules and the thread's saved messages.
    """
    session_id = thread["session_id"]
    has_memory = cli_session_exists(session_id)
    session_args = (["--resume", session_id] if has_memory
                    else ["--session-id", session_id])
    answered = any(m.get("role") == "assistant" for m in thread["messages"])
    seeded = has_memory and answered
    history = [] if has_memory else thread["messages"]
    chat_store.append(account_id, session_id, "user", message[:prompt.MAX_CHARS])

    def _turn() -> str:
        system, user = prompt.build(account_id, message, seeded, history)
        paths.CHAT_CWD.mkdir(parents=True, exist_ok=True)
        reply = ClaudeCliBackend(model=CHAT_MODEL, effort=EFFORT_MEDIUM, stage="chat").call(
            system, user, extra_args=prompt.TOOL_ARGS + session_args,
            cwd=str(paths.CHAT_CWD),
            env_extra={"PYTHONPATH": str(paths.ROOT),
                       "PDASH_CHAT_ACCOUNT": account_id,
                       "PDASH_CHAT_SIG": security.chat_binding(account_id)})
        chat_store.append(account_id, session_id, "assistant", reply)
        return reply

    job_id, _ = RUNNER.start(session_id, _turn, account_id=account_id)
    return job_id
