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

import os
import re
import threading
import time
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core import paths
from ..portfolio import security
from ..core.llm import SLOW_MODEL, ClaudeCliBackend
from ..portfolio import service
from ..portfolio.store import chat_store
from .accounts import require_account

router = APIRouter(prefix="/api/accounts", tags=["chat"])

MAX_CHARS = 4000      # per message, guard against a giant paste
HISTORY_CHARS = 20_000      # saved messages resent when the CLI memory is gone
NEWS_N = 20
# Display rule, not a timeout: the CLI call itself is unbounded. Past this the
# poller reports 'timed out' and a new turn may start on the session. Sized
# above the longest honest run (a screen plus a dozen per-symbol lookups);
# chat has no measured latency distribution yet.
_STALE_S = 1800
_JOB_TTL_S = 3600            # finished jobs are pruned after an hour

SYSTEM = """당신은 개인 투자자의 포트폴리오 대시보드에 내장된 분석 어시스턴트입니다.
아래 [계좌 현황]과 [뉴스 헤드라인]은 방금 조회한 실제 데이터입니다.

조회 도구:
스냅샷에 없는 데이터가 필요하면 Bash로 아래 명령을 실행해 JSON으로 조회한다.
반드시 이 명령만, 따옴표·환경변수·파이프 없이 그대로 쓴다. 다른 명령·파일 접근은 거부된다.
[이 계좌] — 이 대화가 열린 계좌 하나만 읽는다. 다른 계좌는 어떤 방법으로도 볼 수 없다.
  $TOOL account                       # 계좌 정보·보유 전체·통화별 현금
  $TOOL trades since=2026-01-01 until=2026-03-31 symbol=KR:005930 limit=50 offset=0
      # 거래 기록, 최신순. 옵션은 모두 선택
  $TOOL snapshots currency=KRW days=365  # 일별 평가금액·원가·손익·현금·총자산
  $TOOL chats                         # 이 계좌의 다른 대화 목록
  $TOOL chat <session_id>             # 그 대화 내용
[종목·시장]
  $TOOL search <검색어>               # 종목 검색 (이름/코드)
  $TOOL quote KR:005930               # 현재가
  $TOOL chart US:AAPL                 # 차트 지표 요약
  $TOOL fundamental KR:005930         # 재무·밸류에이션
  $TOOL verdict KR:005930 [2026-10-01]  # 저장된 AI 종합 분석 (날짜를 주면 그날 것)
  $TOOL screen per_max=8 pbr_max=1 div_min=3 mcap_min=5000
      # 한국 전 종목 조건 스크리닝 (종목 발굴). 키: per_min/per_max, pbr_min/pbr_max,
      # div_min(배당%), eps_min, mcap_min/mcap_max(억원), value_min(거래대금 억원),
      # market=KOSPI|KOSDAQ, sort=per|pbr|div|mcap|value, limit(최대 20)
  처음 보는 종목에 chart·fundamental을 부르면 일봉·재무를 받아 공유 시장 DB에 저장한다
  (계좌 장부는 건드리지 않는다).
[뉴스] — 수집한 기사 전부 (모든 분야·날짜, 분야 없는 기사 포함)
  $TOOL news KR:005930                # 종목이 속한 분야 뉴스(거시 포함) + 제목에 이름이 나온 기사
  $TOOL news_search 낱말 낱말 theme=semiconductor since=2026-09-01 until=2026-09-30 source=etnews_it min_importance=4 limit=20 offset=0
      # 기사 검색. 낱말은 제목·요약·본문에서 그대로 찾는다. theme=none 이면 분야 없는 기사
  $TOOL article <signal_id>           # 기사 하나의 저장된 칸 전부
  $TOOL theme semiconductor           # 분야의 지금 (7일 주요 기사, 분위기, 지켜볼 것, 주간·월간 요약)
  $TOOL theme_history semiconductor kind=daily|weekly|monthly since=… until=… limit=30 offset=0
  $TOOL catalog                       # 분야 목록과 정의, 소스 목록과 수집 상태
  $TOOL theme_symbols semiconductor days=7
      # 분야에 걸린 종목. tagged=종목에 붙은 분야, mentioned=최근 뉴스 본문에 이름이 나온 종목(오탐 가능)
symbol_id 규약: 한국 = KR:6자리코드, 미국 = US:티커. 모르면 먼저 search로 찾는다.
분야 id는 catalog로 확인한다. 결과가 길면 `_truncated`가 붙는다 — 조건을 좁히거나 offset으로 이어 받는다.
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



# The one command the agent may run. Absolute, because the CLI runs in the
# empty CHAT_CWD (PYTHONPATH points `-m src…` back at the repo). Bash is the
# only tool it gets and this the only command dontAsk lets through; any other
# command, and every file outside the empty cwd, is refused (core/llm.py,
# tool lockdown). No turn cap (see the module docstring).
# The account commands read the account named in the environment, signed with
# security.chat_binding — no command takes an account id.
TOOL_CMD = f"{paths.ROOT / '.venv' / 'bin' / 'python'} -m src.portfolio.chat.tools"
TOOL_ARGS = ["--tools", "Bash", "--allowedTools", f"Bash({TOOL_CMD}:*)"]
SYSTEM = SYSTEM.replace("$TOOL", TOOL_CMD)


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

    Only the chat cwd's project counts: --resume finds a session by the
    directory it runs in, so a transcript elsewhere (a turn from before the
    cwd moved) is one it cannot resume. The CLI names a project after its
    path with every non-alphanumeric character turned into '-'.
    """
    project = re.sub(r"[^A-Za-z0-9]", "-", str(paths.CHAT_CWD))
    return os.path.exists(os.path.expanduser(
        f"~/.claude/projects/{project}/{session_id}.jsonl"))


# ── prompt assembly ───────────────────────────────────────────────────

def _fmt(n, ccy: str) -> str:
    if n is None:
        return "-"
    sym = "₩" if ccy == "KRW" else ("$" if ccy == "USD" else ccy + " ")
    return f"{sym}{n:,.0f}" if ccy == "KRW" else f"{sym}{n:,.2f}"


# The badge's words (shared.js LABEL_KO), so the answer and the card agree.
LABEL_KO = {"strong_buy": "적극 긍정", "buy": "긍정", "neutral": "중립",
            "sell": "부정", "strong_sell": "적극 부정"}


def _opinion(v: dict, held: bool, price, ccy: str) -> str:
    """' | 종합의견 …' for one holding, or '' when nothing is stored.

    Same reading as the card's coordinate line (account.html coordLineText):
    a holding gets stop and target, a watch row the entry zone as well, each
    against the current price. A facts-only result is not an opinion."""
    if not v or not v.get("date"):
        return ""
    if v.get("status") == "facts_only":
        return f" | 종합의견 판단 없음(사실만, {v['date']})"
    ko = LABEL_KO.get(v.get("label"))
    if not ko:
        return ""

    def at(x):
        pct = f" ({(x / price - 1) * 100:+.1f}%)" if price else ""
        return _fmt(x, ccy) + pct

    c = v.get("coords") or {}
    bits = []
    if not held and c.get("entry_low") is not None and c.get("entry_high") is not None:
        bits.append(f"진입구간 {_fmt(c['entry_low'], ccy)}~{_fmt(c['entry_high'], ccy)}")
    if c.get("stop") is not None:
        bits.append("손절 " + at(c["stop"]))
    if c.get("target") is not None:
        bits.append("목표 " + at(c["target"]))
    return f" | 종합의견 {ko} ({v['date']})" + "".join(" · " + b for b in bits)


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
        from ..research import analyze
        verdicts = analyze.summary(sids)
    except Exception:  # noqa: BLE001 — chat must survive a missing verdict dir
        verdicts = {}

    lines.append("[보유·관심 종목]")
    for h in holdings:
        ccy = h.get("currency") or "KRW"
        name = h.get("name_kr") or h.get("name_en") or h["symbol_id"]
        price, avg, qty = h.get("price"), h.get("avg_cost"), h.get("qty")
        pnl_pct = ((price - avg) / avg * 100) if (price and avg) else None
        hold = (f"{qty}주 @{_fmt(avg, ccy)}" if qty else "관심(미보유)")
        verdict = _opinion(verdicts.get(h["symbol_id"]) or {}, bool(qty), price, ccy)
        etf = " [ETF]" if h.get("is_etf") else ""
        lines.append(
            f"  {name} {h['symbol_id']}{etf} | {hold} | 현재 {_fmt(price, ccy)}"
            + (f" ({pnl_pct:+.1f}%)" if pnl_pct is not None else "")
            + (f" | 섹터 {h['sector']}" if h.get("sector") else "")
            + verdict
        )
    return "\n".join(lines)


def _news_context() -> str:
    from ..research.news import read
    try:
        items = (read.feed(limit=NEWS_N).get("news") or [])[:NEWS_N]
    except Exception:  # noqa: BLE001 — news is optional context
        return ""
    if not items:
        return ""
    lines = ["[뉴스 헤드라인 · 최근 수집]"]
    for it in items:
        imp = f" ★{it['importance']}" if it.get("importance") else ""
        lines.append(f"  ({it.get('theme_kr')}){imp} {it.get('title')}")
    return "\n".join(lines)


def _build(account_id: str, message: str, seeded: bool,
           history: list | None = None) -> tuple[str, str]:
    """(system, user) for this turn.

    Once the session holds a real exchange it also holds the rules and the
    history, so a later turn sends neither. It still sends a fresh account
    snapshot, because prices and verdicts move between turns and the stale
    copy in the session would otherwise be the model's only reference.

    `history` is the thread's saved messages when the CLI has no memory of
    them — the newest ones, capped so a long thread cannot crowd the prompt.
    """
    q = str(message)[:MAX_CHARS]
    if seeded:
        return "", "\n\n".join((_account_context(account_id),
                                "[사용자 질문]\n" + q))
    past = ""
    if history:
        lines = [("사용자: " if m.get("role") == "user" else "답변: ") + str(m.get("content", ""))
                 for m in history]
        past = "[이전 대화]\n" + "\n\n".join(lines)[-HISTORY_CHARS:]
    return SYSTEM, "\n\n".join(x for x in (
        _account_context(account_id), _news_context(), past,
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
    # A third: the CLI transcripts are deleted every midnight, so an old
    # thread can have answers on screen and no memory behind them. That turn
    # starts a new session with the rules and the thread's saved messages.
    has_memory = _cli_session_exists(body.session_id)
    session_args = (["--resume", body.session_id] if has_memory
                    else ["--session-id", body.session_id])
    answered = any(m.get("role") == "assistant" for m in thread["messages"])
    seeded = has_memory and answered
    history = [] if has_memory else thread["messages"]
    chat_store.append(account_id, body.session_id, "user", msg[:MAX_CHARS])

    def _run() -> None:
        try:
            system, user = _build(account_id, msg, seeded, history)
            paths.CHAT_CWD.mkdir(parents=True, exist_ok=True)
            reply = ClaudeCliBackend(model=SLOW_MODEL, stage="chat").call(
                system, user, extra_args=TOOL_ARGS + session_args,
                cwd=str(paths.CHAT_CWD),
                env_extra={"PYTHONPATH": str(paths.ROOT),
                           "PDASH_CHAT_ACCOUNT": account_id,
                           "PDASH_CHAT_SIG": security.chat_binding(account_id)})
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
