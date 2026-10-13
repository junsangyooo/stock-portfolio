"""/api/accounts/{id}/chat* — routes for the account chat.

The turn itself (prompt, account snapshot, CLI session, background job) is
portfolio/chat/. Every route here is behind require_account: a reply embeds
one account's holdings, PnL and cash, so any account's session must not be
able to read another's.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..portfolio.chat import session
from ..portfolio.store import chat_store
from .accounts import require_account

router = APIRouter(prefix="/api/accounts", tags=["chat"])


# ── threads (CRUD) ────────────────────────────────────────────────────

class ThreadCreate(BaseModel):
    title: str | None = None


@router.get("/{account_id}/chats")
def list_chats(account_id: str, _: str = Depends(require_account)) -> dict:
    return {"ok": True, "chats": chat_store.list_for(account_id)}


@router.post("/{account_id}/chats")
def create_chat(account_id: str, body: ThreadCreate | None = None,
                _: str = Depends(require_account)) -> dict:
    sid = chat_store.create(account_id, (body.title if body else None) or "새 대화")
    return {"ok": True, "session_id": sid}


@router.get("/{account_id}/chats/{session_id}")
def get_chat(account_id: str, session_id: str,
             _: str = Depends(require_account)) -> dict:
    t = chat_store.get(account_id, session_id)
    if not t:
        raise HTTPException(404, "chat not found")
    return {"ok": True, "chat": t, "running": session.RUNNER.running(session_id)}


@router.delete("/{account_id}/chats/{session_id}")
def delete_chat(account_id: str, session_id: str,
                _: str = Depends(require_account)) -> dict:
    if not chat_store.delete(account_id, session_id):
        raise HTTPException(404, "chat not found")
    session.RUNNER.forget(session_id)
    return {"ok": True}


# ── ask (async job) ───────────────────────────────────────────────────

class ChatIn(BaseModel):
    session_id: str
    message: str


@router.post("/{account_id}/chat")
def chat(account_id: str, body: ChatIn,
         _: str = Depends(require_account)) -> dict:
    thread = chat_store.get(account_id, body.session_id)
    if not thread:
        raise HTTPException(404, "chat not found")
    msg = (body.message or "").strip()
    if not msg:
        return {"ok": False, "detail": "empty message"}
    if session.RUNNER.running(body.session_id):
        return {"ok": False, "detail": "이 대화에 이미 실행 중인 요청이 있습니다"}
    return {"ok": True, "job_id": session.ask(account_id, thread, msg)}


@router.get("/{account_id}/chat/status")
def chat_status(account_id: str, job_id: str,
                _: str = Depends(require_account)) -> dict:
    j = session.RUNNER.get(job_id)
    if not j or j.get("account_id") != account_id:
        # Unknown to this account: either never existed, pruned after the
        # answer was persisted, or another account's. The client falls back
        # to reloading the thread, where a finished answer is waiting.
        return {"ok": True, "state": "unknown"}
    if j["state"] == "stale":
        return {"ok": True, "state": "error", "detail": "job timed out"}
    return {"ok": True, "state": j["state"], "reply": j["result"],
            "detail": j["error"], "started_at": j["started_at"]}
