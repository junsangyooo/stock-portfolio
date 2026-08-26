"""/api/accounts/{id}/chat* — portfolio Q&A backed by the Claude CLI.

Threads: a chat is a row in ``chat_sessions`` whose ``session_id`` is also the
Claude CLI session id. The first turn runs ``--session-id <uuid>``, every later
turn ``--resume <uuid>``, so the model keeps its own transcript — tool lookups
and their raw results included — and never re-fetches what it already pulled.
Our own copy of the messages exists only to repaint the browser.

Async: the request never waits for the model. POST /chat spawns a thread and
returns a job id; the browser polls GET /chat/status. This is the same shape as
analysis_jobs.py, and for the same reason — Cloudflare gives up on an origin
response at ~100s, so no synchronous route can ever hold a claude -p run. The
old ``--max-turns 8`` existed purely to squeeze the agent loop under that
ceiling; with the wait gone, the loop is uncapped and heavy questions (screen →
per-candidate fundamentals) can take as long as they need.

Billing: ClaudeCliBackend (subscription), never the paid API. See ops notes §7.
"""
from __future__ import annotations

import glob
import os
import threading
import time
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..agents.llm_backend import SLOW_MODEL, ClaudeCliBackend
from ..portfolio import service
from ..portfolio.store import chat_store
from . import analysis
from .portfolio_api import require_account

router = APIRouter(prefix="/api/accounts", tags=["chat"])

MAX_CHARS = 4000      # per message, guard against a giant paste
NEWS_N = 20
# Hang guard only — nothing blocks on this now, so it is sized for the worst
# honest run (a screen plus a dozen per-symbol lookups), not for an HTTP wait.
TIMEOUT_S = 1800
_STALE_S = TIMEOUT_S + 120   # a job still 'running' past this is dead
_JOB_TTL_S = 3600            # finished jobs are pruned after an hour

SYSTEM = """당신은 개인 투자자의 포트폴리오 대시보드에 내장된 분석 어시스턴트입니다.
아래 [계좌 현황]과 [뉴스 헤드라인]은 방금 조회한 실제 데이터입니다.

추가 조회 도구 (읽기 전용):
스냅샷에 없는 데이터가 필요하면 Bash로 아래 명령을 실행해 JSON으로 조회할 수 있다.
반드시 이 명령만 사용한다 (다른 명령·파일 접근 금지):
  .venv/bin/python -m src.agents.chat_tools search <검색어>        # 종목 검색 (이름/코드)
  .venv/bin/python -m src.agents.chat_tools quote KR:005930        # 현재가
  .venv/bin/python -m src.agents.chat_tools chart US:AAPL          # 차트 지표 요약
  .venv/bin/python -m src.agents.chat_tools fundamental KR:005930  # 재무·밸류에이션
  .venv/bin/python -m src.agents.chat_tools verdict KR:005930      # 최신 AI 종합 판단
  .venv/bin/python -m src.agents.chat_tools news KR:005930         # 연결 뉴스·이슈
  .venv/bin/python -m src.agents.chat_tools screen per_max=8 pbr_max=1 div_min=3 mcap_min=5000
      # 한국 전 종목 조건 스크리닝 (종목 발굴). 키: per_min/per_max, pbr_min/pbr_max,
      # div_min(배당%), eps_min, mcap_min/mcap_max(억원), value_min(거래대금 억원),
      # market=KOSPI|KOSDAQ, sort=per|pbr|div|mcap|value, limit(최대 20)
  .venv/bin/python -m src.agents.chat_tools theme_symbols semiconductor days=7
      # 테마에 걸린 종목 (테마 id: battery ai_sw bio semiconductor robotics space
      # fintech shipbuilding adv_manufacturing macro 등). tagged=보유 기반 태깅,
      # mentioned=최근 뉴스 본문에 이름이 나온 종목(발굴용, 오탐 가능)
symbol_id 규약: 한국 = KR:6자리코드, 미국 = US:티커. 모르면 먼저 search로 찾는다.
발굴 요청("~한 종목 찾아줘")은 screen으로 후보를 추린 뒤, 상위 종목만 fundamental/verdict로
심화 조회해 근거를 붙인다. 미국 전체 스크리닝은 미지원 — 한국만 가능하다고 안내한다.
질문에 필요한 만큼 조회한다. 호출당 수 초 걸리지만 시간 제한은 없으니, 근거가 모자란 채로
답하기보다 필요한 조회를 다 하고 답한다. 이미 이 대화에서 조회한 값은 다시 조회하지 않는다.

규칙:
- 한국어로, 군더더기 없이 답한다. 인사말·과잉 사과 금지.
- 주어진 데이터·도구 조회 결과에 없는 수치는 지어내지 않는다. 조회로도 없으면 없다고 말한다.
- 숫자를 인용할 때는 데이터의 값을 그대로 쓴다.
- 매수/매도 단정 대신 근거와 리스크를 함께 제시한다.
- 답변은 기본 5줄 이내. 표·목록을 요청받으면 그때 길게 쓴다.
- 서식은 **굵게**, `코드`, 불렛(- ), 구분선(---), 표(| 구분) 다섯 가지만 쓴다.
  헤딩(#)·링크([]())·인용(>)은 쓰지 않는다 — 화면이 그대로 기호로 보인다."""

# Sent again on every later turn: SYSTEM only goes out on a thread's first
# turn, so a conversation opened before this existed would never learn the
# format and the hand-off would silently do nothing in it.
PLAN_CONTRACT = """계획 노트 담기:
사용자가 매매 계획을 계획 노트에 넣어달라고 **명시적으로 요청했을 때만**, 답변 맨 끝에
아래 블록을 정확히 이 형식으로 덧붙인다. 화면이 이 블록을 지우고 그 자리에
「노트에 담기」 버튼이 달린 카드로 바꿔 보여준다. 저장은 사용자가 그 버튼을 눌러야 일어난다.
```plan
[{"symbol_id": "KR:247540", "name": "에코프로비엠", "side": "buy", "price": 900000, "qty": 3, "note": "근거 한 줄"}]
```
- 블록 안에는 JSON 배열 하나만 쓴다. 계획이 여러 건이면 원소를 늘린다.
- side는 "buy" 또는 "sell". price·qty는 숫자만 — 콤마·통화기호·단위를 넣지 않는다.
- note는 선택. symbol_id를 모르면 먼저 search로 확인한다. 추측해서 쓰지 않는다.
- 요청받지 않았으면 이 블록을 내보내지 않는다. 계획을 제안만 하는 답변에는 붙이지 않는다."""

SYSTEM = SYSTEM + "\n\n" + PLAN_CONTRACT


# claude -p 도구 허용: 위 조회 CLI 한 가지만. 그 외 Bash·파일·네트워크는
# 헤드리스 모드에서 자동 거부된다. 턴 수 제한 없음 (모듈 docstring 참고).
TOOL_ARGS = [
    "--allowedTools", "Bash(.venv/bin/python -m src.agents.chat_tools:*)",
]


# ── job registry ──────────────────────────────────────────────────────
# job_id -> {state, account_id, session_id, reply, detail, started_at, _t}
# Single uvicorn worker (server.py), so an in-memory dict is shared across
# requests. Jobs are transient: the answer is persisted to chat_sessions the
# moment it lands, so losing the registry on restart costs nothing but the
# poll in flight.
_jobs: dict = {}
_lock = threading.Lock()


def _prune_locked() -> None:
    now = time.time()
    for k in [k for k, j in _jobs.items()
              if j["state"] != "running" and now - j["_t"] > _JOB_TTL_S]:
        _jobs.pop(k, None)


def _running_for(session_id: str) -> str | None:
    now = time.time()
    for jid, j in _jobs.items():
        if (j["session_id"] == session_id and j["state"] == "running"
                and now - j["_t"] <= _STALE_S):
            return jid
    return None


def _cli_session_exists(session_id: str) -> bool:
    """Whether the CLI already has a transcript for this id.

    Ground truth for --session-id vs --resume: our message log can disagree
    (a first turn that died after the CLI wrote its file would otherwise be
    retried with --session-id and rejected as already taken).
    """
    return bool(glob.glob(os.path.expanduser(
        f"~/.claude/projects/*/{session_id}.jsonl")))


# ── prompt assembly ───────────────────────────────────────────────────

def _fmt(n, ccy: str) -> str:
    if n is None:
        return "-"
    sym = "₩" if ccy == "KRW" else ("$" if ccy == "USD" else ccy + " ")
    return f"{sym}{n:,.0f}" if ccy == "KRW" else f"{sym}{n:,.2f}"


def _account_context(account_id: str) -> str:
    agg = service.aggregate(account_id, spark_n=2)
    if not agg.get("exists"):
        return f"[계좌 {account_id}] 존재하지 않음"

    lines = [f"[계좌 현황] {account_id}"]
    for ccy, t in (agg.get("totals") or {}).items():
        lines.append(
            f"  {ccy}: 평가 {_fmt(t.get('eval'), ccy)} / "
            f"손익 {_fmt(t.get('pnl'), ccy)} ({t.get('pnl_pct', 0):+.2f}%) / "
            f"예수금 {_fmt(t.get('cash'), ccy)} / "
            f"총자산 {_fmt(t.get('total_assets'), ccy)} / "
            f"보유 {t.get('n_held')}종목 (추적 {t.get('n_tracked')})"
        )

    holdings = agg.get("holdings") or []
    sids = [h["symbol_id"] for h in holdings]
    try:
        verdicts = analysis.analysis_summary(",".join(sids)).get("symbols", {})
    except Exception:  # noqa: BLE001 — chat must survive a missing verdict dir
        verdicts = {}

    lines.append("[보유·관심 종목]")
    for h in holdings:
        ccy = h.get("currency") or "KRW"
        name = h.get("name_kr") or h.get("name_en") or h["symbol_id"]
        price, avg, qty = h.get("price"), h.get("avg_cost"), h.get("qty")
        pnl_pct = ((price - avg) / avg * 100) if (price and avg) else None
        hold = (f"{qty}주 @{_fmt(avg, ccy)}" if qty else "관심(미보유)")
        v = verdicts.get(h["symbol_id"]) or {}
        verdict = f" | 종합의견 {v['action_ko']} ({v.get('date')})" if v.get("action_ko") else ""
        etf = " [ETF]" if h.get("is_etf") else ""
        lines.append(
            f"  {name} {h['symbol_id']}{etf} | {hold} | 현재 {_fmt(price, ccy)}"
            + (f" ({pnl_pct:+.1f}%)" if pnl_pct is not None else "")
            + (f" | 섹터 {h['sector']}" if h.get("sector") else "")
            + verdict
        )
    return "\n".join(lines)


def _news_context() -> str:
    from ..agents.news import feed as news_feed
    try:
        items = (news_feed.build(limit=NEWS_N).get("news") or [])[:NEWS_N]
    except Exception:  # noqa: BLE001 — news is optional context
        return ""
    if not items:
        return ""
    lines = ["[뉴스 헤드라인 · 최근 수집]"]
    for it in items:
        imp = f" ★{it['importance']}" if it.get("importance") else ""
        lines.append(f"  ({it.get('theme_kr')}){imp} {it.get('title')}")
    return "\n".join(lines)


def _build(account_id: str, message: str, seeded: bool) -> tuple[str, str]:
    """(system, user) for this turn.

    Once the session holds a real exchange it also holds the rules and the
    history, so a later turn sends neither. It still sends a fresh account
    snapshot, because prices and verdicts move between turns and the stale
    copy in the session would otherwise be the model's only reference.
    """
    q = str(message)[:MAX_CHARS]
    if seeded:
        return "", "\n\n".join((_account_context(account_id), PLAN_CONTRACT,
                                "[사용자 질문]\n" + q))
    return SYSTEM, "\n\n".join(x for x in (
        _account_context(account_id), _news_context(),
        "[사용자 질문]\n" + q) if x)


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
    return {"ok": True, "chat": t, "running": bool(_running_for(session_id))}


@router.delete("/{account_id}/chats/{session_id}")
def delete_chat(account_id: str, session_id: str,
                _: str = Depends(require_account)) -> dict:
    if not chat_store.delete(account_id, session_id):
        raise HTTPException(404, "chat not found")
    with _lock:
        for jid in [j for j, v in _jobs.items() if v["session_id"] == session_id]:
            _jobs.pop(jid, None)
    return {"ok": True}


# ── ask (async job) ───────────────────────────────────────────────────

class ChatIn(BaseModel):
    session_id: str
    message: str


@router.post("/{account_id}/chat")
def chat(account_id: str, body: ChatIn,
         _: str = Depends(require_account)) -> dict:
    # require_account, not just the server-wide /api gate: the reply embeds
    # this account's holdings, PnL and cash, so any account's session must not
    # be able to read another's.
    thread = chat_store.get(account_id, body.session_id)
    if not thread:
        raise HTTPException(404, "chat not found")
    msg = (body.message or "").strip()
    if not msg:
        return {"ok": False, "detail": "empty message"}

    with _lock:
        _prune_locked()
        if _running_for(body.session_id):
            return {"ok": False, "detail": "이 대화에 이미 실행 중인 요청이 있습니다"}
        job_id = str(uuid.uuid4())
        _jobs[job_id] = {
            "state": "running", "account_id": account_id,
            "session_id": body.session_id, "reply": None, "detail": None,
            "started_at": datetime.utcnow().isoformat() + "Z", "_t": time.time(),
        }

    # Two separate questions. Which flag: does the CLI already know this id?
    # Whether to re-send the rules: has an answer actually landed? A first turn
    # that died may have left a session behind without the rules in it, and
    # resuming that one silently strips the model's tools and format.
    session_args = (["--resume", body.session_id]
                    if _cli_session_exists(body.session_id)
                    else ["--session-id", body.session_id])
    seeded = any(m.get("role") == "assistant" for m in thread["messages"])
    chat_store.append(account_id, body.session_id, "user", msg[:MAX_CHARS])

    def _run() -> None:
        try:
            system, user = _build(account_id, msg, seeded)
            reply = ClaudeCliBackend(model=SLOW_MODEL).call(
                system, user, timeout_s=TIMEOUT_S,
                extra_args=TOOL_ARGS + session_args)
            chat_store.append(account_id, body.session_id, "assistant", reply)
            with _lock:
                if job_id in _jobs:
                    _jobs[job_id].update(state="done", reply=reply, _t=time.time())
        except Exception as e:  # noqa: BLE001 — surface to the poller, never crash
            with _lock:
                if job_id in _jobs:
                    _jobs[job_id].update(state="error", detail=str(e)[:300],
                                         _t=time.time())

    threading.Thread(target=_run, daemon=True, name=f"chat:{job_id}").start()
    return {"ok": True, "job_id": job_id}


@router.get("/{account_id}/chat/status")
def chat_status(account_id: str, job_id: str,
                _: str = Depends(require_account)) -> dict:
    with _lock:
        j = _jobs.get(job_id)
        if not j or j["account_id"] != account_id:
            # Unknown to this account: either never existed, pruned after the
            # answer was persisted, or another account's. The client falls back
            # to reloading the thread, where a finished answer is waiting.
            return {"ok": True, "state": "unknown"}
        if j["state"] == "running" and time.time() - j["_t"] > _STALE_S:
            return {"ok": True, "state": "error", "detail": "job timed out"}
        return {"ok": True, "state": j["state"], "reply": j.get("reply"),
                "detail": j.get("detail"), "started_at": j.get("started_at")}
