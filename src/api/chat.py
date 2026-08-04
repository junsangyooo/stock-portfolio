"""/api/accounts/{id}/chat — portfolio Q&A backed by the Claude CLI.

The browser ships only the conversation; the account snapshot (holdings,
cash, judge verdicts) and the latest news headlines are assembled here on
every request. Nothing is persisted — a page reload starts a new chat.

Billing: ClaudeCliBackend (subscription), never the paid API. See ops notes §7.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..agents.llm_backend import SLOW_MODEL, ClaudeCliBackend
from ..portfolio import service
from . import analysis
from .portfolio_api import require_account

router = APIRouter(prefix="/api/accounts", tags=["chat"])

MAX_TURNS = 10        # keep the tail of the conversation, drop older turns
MAX_CHARS = 4000      # per message, guard against a giant paste
NEWS_N = 20
TIMEOUT_S = 240

SYSTEM = """당신은 개인 투자자의 포트폴리오 대시보드에 내장된 분석 어시스턴트입니다.
아래 [계좌 현황]과 [뉴스 헤드라인]은 방금 조회한 실제 데이터입니다.

규칙:
- 한국어로, 군더더기 없이 답한다. 인사말·과잉 사과 금지.
- 주어진 데이터에 없는 수치는 지어내지 않는다. 모르면 모른다고 말한다.
- 숫자를 인용할 때는 위 데이터의 값을 그대로 쓴다.
- 매수/매도 단정 대신 근거와 리스크를 함께 제시한다.
- 답변은 기본 5줄 이내. 표·목록을 요청받으면 그때 길게 쓴다."""


class ChatIn(BaseModel):
    messages: list[dict]   # [{"role": "user"|"assistant", "content": str}]


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


@router.post("/{account_id}/chat")
def chat(account_id: str, body: ChatIn,
         _: str = Depends(require_account)) -> dict:
    # require_account, not just the server-wide /api gate: the reply embeds
    # this account's holdings, PnL and cash, so any account's session must not
    # be able to read another's.
    turns = [m for m in body.messages
             if m.get("role") in ("user", "assistant") and m.get("content")][-MAX_TURNS:]
    if not turns:
        return {"ok": False, "detail": "empty conversation"}

    convo = "\n\n".join(
        ("사용자: " if m["role"] == "user" else "어시스턴트: ")
        + str(m["content"])[:MAX_CHARS]
        for m in turns
    )
    user = "\n\n".join(x for x in (
        _account_context(account_id), _news_context(),
        "[대화]\n" + convo, "위 대화의 마지막 사용자 질문에 답하세요.") if x)

    try:
        reply = ClaudeCliBackend(model=SLOW_MODEL).call(
            SYSTEM, user, timeout_s=TIMEOUT_S)
    except Exception as e:  # noqa: BLE001 — surface CLI faults to the UI
        return {"ok": False, "detail": str(e)[:300]}
    return {"ok": True, "reply": reply}
