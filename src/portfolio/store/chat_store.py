"""chat_sessions table CRUD.

One row per chat thread. ``session_id`` is a uuid4 that doubles as the Claude
CLI session id (``--session-id`` on the first turn, ``--resume`` after), so the
model keeps its own transcript — including tool lookups and their results —
next to ours. Our ``messages`` copy exists to repaint the browser; the model's
memory lives in the CLI session file.

Deleting a thread must therefore clear both: the row here and the CLI's
``~/.claude/projects/<cwd>/<session_id>.jsonl``.
"""
from __future__ import annotations

import glob
import json
import logging
import os
import time
import uuid
from typing import List, Optional

from .db import connect

_log = logging.getLogger("chat_store")

TITLE_CHARS = 40


def _title(text: str) -> str:
    t = " ".join(text.split())[:TITLE_CHARS]
    return t or "새 대화"


def create(account_id: str, title: str = "새 대화") -> str:
    """Insert an empty thread and return its session_id (uuid4)."""
    sid, now = str(uuid.uuid4()), int(time.time())
    with connect() as c:
        c.execute(
            """
            INSERT INTO chat_sessions (session_id, account_id, title, messages,
                                       created_at, updated_at)
            VALUES (?, ?, ?, '[]', ?, ?)
            """,
            (sid, account_id, _title(title), now, now),
        )
        c.commit()
    return sid


def list_for(account_id: str) -> List[dict]:
    """Threads newest-first, without the transcript (the list view only needs
    a title and a count)."""
    with connect() as c:
        rows = c.execute(
            """
            SELECT session_id, title, created_at, updated_at, messages
            FROM chat_sessions WHERE account_id = ?
            ORDER BY updated_at DESC
            """,
            (account_id,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        # counted here, not with json_array_length(): the JSON1 extension is
        # not guaranteed on the host's SQLite build and threads are tiny.
        try:
            d["n"] = len(json.loads(d.pop("messages") or "[]"))
        except json.JSONDecodeError:
            d["n"] = 0
        out.append(d)
    return out


def get(account_id: str, session_id: str) -> Optional[dict]:
    """One thread with its transcript, or None. account_id is part of the
    lookup, not a post-check: a session of another account must read as
    missing, never as forbidden-but-present."""
    with connect() as c:
        row = c.execute(
            "SELECT * FROM chat_sessions WHERE session_id = ? AND account_id = ?",
            (session_id, account_id),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        d["messages"] = json.loads(d.get("messages") or "[]")
    except json.JSONDecodeError:  # corrupt row — repaint empty rather than 500
        _log.warning("chat_sessions.messages not JSON (session=%s)", session_id)
        d["messages"] = []
    return d


def append(account_id: str, session_id: str, role: str, content: str) -> None:
    """Append one message. The first user message also names the thread."""
    cur = get(account_id, session_id)
    if cur is None:
        return
    msgs = cur["messages"] + [{"role": role, "content": content}]
    title = cur["title"]
    if role == "user" and not cur["messages"]:
        title = _title(content)
    with connect() as c:
        c.execute(
            "UPDATE chat_sessions SET messages = ?, title = ?, updated_at = ? "
            "WHERE session_id = ? AND account_id = ?",
            (json.dumps(msgs, ensure_ascii=False), title, int(time.time()),
             session_id, account_id),
        )
        c.commit()


def delete(account_id: str, session_id: str) -> int:
    """Drop the row and the CLI's session file. Returns rows deleted.

    The file is found by glob on the uuid instead of rebuilding the CLI's
    cwd-to-directory encoding — the uuid is unique, the encoding is not ours.
    """
    with connect() as c:
        cur = c.execute(
            "DELETE FROM chat_sessions WHERE session_id = ? AND account_id = ?",
            (session_id, account_id))
        c.commit()
        n = cur.rowcount
    if n:
        for p in glob.glob(os.path.expanduser(
                f"~/.claude/projects/*/{session_id}.jsonl")):
            try:
                os.remove(p)
            except OSError as e:  # noqa: PERF203 — a stuck file must not fail the delete
                _log.warning("could not remove CLI session file %s: %s", p, e)
    return n
