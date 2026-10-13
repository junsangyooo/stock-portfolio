"""What the chat agent is told about the account on every turn.

The snapshot is assembled here, from the account book and the shared
analysis, and nowhere else: the model sees exactly what the account page
shows (holdings, totals, each holding's stored opinion), never a raw row.
"""
from __future__ import annotations

from .. import service

NEWS_N = 20     # headlines on a turn that opens a CLI session


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


def account_context(account_id: str) -> str:
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
        from ...research import analyze
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


def news_context() -> str:
    from ...research.news import read
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
