"""One account's plan pass — read the book, write the diff, apply what holds.

The symbol judgment is already made and stored (src/research/analyze); it says
nothing about who owns anything, which is what lets one stored result serve
both books. This is the other half: what that judgment means for THIS account,
given what it holds, at what price, with how much cash, under which plans.

One account is one process is one `claude -p` session, created here and never
resumed. Two accounts cannot see each other's context because there is no
shared session, no shared file and no shared row — the isolation is structural,
not a rule the prompt is asked to follow.

What comes back is a diff over the plan notes, not orders. In a human book it
lands as `proposed` and waits for the person; nothing executes it, because
every executor (trade_store, the keeper) matches on `armed`.

    from src.ledger import planner
    with db.connect() as conn:
        planner.run(conn, "junisa", slot="morning")
"""
from __future__ import annotations

import html
import json
import sqlite3
import time
from datetime import datetime, timedelta

from ..core.kst import KST
from ..core.kst import today as _kst_today
from ..research.llm_backend import ClaudeCliBackend, SLOW_MODEL, extract_json
from . import context

# A proposal nobody approved is not a plan. Left alone they pile into the next
# pass's input and the model re-judges its own week-old suggestions.
PROPOSED_TTL_DAYS = 7

# How far a quoted price may sit from the coordinate it claims. Same 1.5% the
# analysis engine uses to anchor its own levels.
ANCHOR_TOLERANCE = 0.015

STANCES = ("hold", "add", "trim", "exit", "watch", "none")
_COND_KINDS = ("immediate", "below", "above", "agent")
_COND_KEYS = ("entry", "take_profit", "stop")
_NO_NOTE = {"stance": "none", "line_ko": "(판단 없음)"}


class IncompleteDiff(Exception):
    """The model left an active plan unjudged. The whole answer is refused —
    a diff that silently drops a plan reads as "keep" and nothing says so."""


SYSTEM_PLAN = """# 계획 작성자

너는 사람 계좌 하나의 계획 노트(주문서)를 오늘 상태에 맞게 고쳐 쓴다. 이 계좌의
주인은 네 제안을 읽고 승인하거나 거절한다. 실행은 주인이 직접 한다.
종목 판단은 끝났다. 각 종목의 분석 결론과 좌표가 입력에 있다. 네 일은 그 결론들을
**이 계좌의 사정**(보유가·수량·현금·비중·기존 계획)과 한자리에 놓고 **무엇을 바꿀지**
정하는 것이다. 종목 하나씩이 아니라 포트폴리오 하나로 본다.

## 네 일이 아닌 것
- 종목 재분석. 분석의 label·좌표·시나리오를 다시 따지지 마라. 분석이 틀렸거나
  낡았다고 보이면 `reanalyze`에 이유를 적고 그 종목의 계획은 건드리지 마라.
- 계산. 비중·손익·좌표까지의 거리·테마 집중도는 이미 계산돼 있다. 인용하라.
- 조회. 도구는 없다. 입력에 없는 것은 모르는 것이다.
- 실행. 너는 diff와 종목별 한 줄만 낸다.

## 입력을 읽는 규칙
- `analysis.freshness`가 `missing`인 종목은 계획을 세우지 않는다. `stale`이면 세우되
  why에 "분석 n일 전"을 적는다.
- `analysis.status`가 `facts_only`면 판정이 없는 결과다. label·score가 숫자로 있어도
  판정으로 읽지 마라.
- 통화가 다른 종목은 다른 totals 아래 있다. 통화 간 비중을 비교하지 마라.
- `price`가 null이면 시세를 모르는 것이다. 그 종목의 pnl·거리도 모른다.
- `watch[]`에는 세 가지가 섞여 있다 — 수량 0인 관심 종목, 관심 목록의 종목, 그리고
  `reason`이 "활성 계획만 있는 종목"인 것(보유하지 않는데 계획이 걸려 있다).
- `totals[].n_cost_unknown`이 0이 아니면 그 통화의 pnl은 그만큼의 종목을 빼고 낸
  값이다.

## 결정 순서
1. **기존 계획부터.** 활성 계획(armed·entered·proposed) 하나하나를 keep / modify /
   abandon 중 하나로 판정한다. 근거는 그 종목의 오늘 좌표(invalidation·targets)와
   reassess_when이다. `source`가 `user`인 계획은 손대지 않는다. 의견은 `suggest`에
   적는다.
2. **계획 없는 보유.** 나갈 조건이 없는 포지션은 미완성이다. 분석에 invalidation이
   있으면 stop을, targets가 있으면 take_profit을 세운다. 좌표가 없으면 `reanalyze`.
3. **신규 진입은 마지막.** 관심 종목과 보유 확대. 현금 비중과 테마 집중도를 본 뒤에.
   매도 계획이 만들 현금은 오늘 매수 한도가 아니다.
4. **포트폴리오 한 번 더.** 매수 계획들의 합이 그 통화 현금을 넘는가, 한 테마에
   몰리는가. 넘치면 확신이 낮은 것부터 뺀다. `add`는 확신이 높은 것부터 적는다.
5. **종목마다 한 줄.** holdings와 watch의 모든 종목에 stance와 line_ko를 쓴다.
   stance: hold(유지) add(추가) trim(축소) exit(정리) watch(관심 유지) none(판단 보류).
   line_ko는 주인이 카드에서 읽는 한 문장 — 이 계좌에서 이 종목이 지금 어떤 상태이고
   오늘 무엇을 보면 되는지. 계획을 바꿨으면 그 요지.

## 지켜야 할 것
- 가격은 그 종목 분석의 좌표에서만. 어느 좌표인지 `basis`에 적는다
  (예: "invalidation", "targets[0]", "support[1]", "entry_zone.low"). 좌표에 없는
  가격이면 basis를 "none"으로 두고 why에 이유를 쓴다. 프로그램이 표시한다.
- 수량은 정수. 매도 수량은 보유 수량 이하.
- `price`는 **기준가**다 — 그 계획으로 실제 사거나 팔 가격. 진입 조건이 있으면 그
  가격, 청산만 거는 계획이면 목표가(take_profit). 비워두지 마라.
- 변경 0건도 답이다.
- why는 "왜 이 값인가"가 보이게. "상승 여력이 있다"는 이유가 아니다.
  "invalidation 66,000이 평단 대비 -5.7%라 손절폭이 스윙에 맞는다"가 이유다.
- 모든 활성 계획은 keep·modify·abandon·suggest 중 **정확히 한 곳에만** 나타난다.
  `suggest`도 그 계획에 대한 판정이다 — 의견을 적었으면 `keep`에 또 넣지 마라.
  빠진 계획이나 두 곳에 든 계획이 있으면 출력 전체가 기각된다.

## 출력
JSON 하나. ```json 코드펜스. 그 뒤에 아무것도 쓰지 마라.
{
  "keep": [],
  "modify": [{"plan_id": 13, "conditions": {}, "qty": 10, "expires": "2026-09-30",
              "basis": {"stop": "invalidation"}, "why": ""}],
  "abandon": [{"plan_id": 14, "why": ""}],
  "suggest": [{"plan_id": 12, "what": "익절 80,000 → 82,000(targets[0])", "why": ""}],
  "add": [{"symbol_id": "KR:005930", "side": "buy", "qty": 3, "price": 71000,
           "conditions": {"entry": {"kind": "below", "price": 71000},
                          "take_profit": {"kind": "above", "price": 82000},
                          "stop": {"kind": "below", "price": 66000}},
           "expires": "2026-09-30",
           "basis": {"entry": "entry_zone.high", "take_profit": "targets[0]",
                     "stop": "invalidation"},
           "why": ""}],
  "reanalyze": [{"symbol_id": "US:GOOGL", "why": ""}],
  "notes_by_symbol": {"KR:005930": {"stance": "hold", "line_ko": ""}},
  "portfolio_note": "두세 문장. 오늘 이 계좌에서 바꾼 것의 요지"
}"""

USER_PLAN = "위 계좌 상태에 대해 계획 diff를 작성하라.\n\n"


# ── validation ────────────────────────────────────────────────────────────

def _levels_of(ctx: dict, symbol_id: str) -> dict:
    for row in ctx["holdings"] + ctx["watch"]:
        if row["symbol_id"] == symbol_id:
            return ((row.get("analysis") or {}).get("levels")) or {}
    return {}


def _coordinate(levels: dict, basis: str):
    """The price a basis string names, or None when it names nothing.

    Accepts what the prompt asks for: "invalidation", "entry_zone.low",
    "targets[0]", "support[1]".
    """
    if not isinstance(basis, str) or not basis or basis == "none":
        return None
    head, _, rest = basis.partition(".")
    idx = None
    if head.endswith("]") and "[" in head:
        head, _, tail = head.partition("[")
        try:
            idx = int(tail[:-1])
        except ValueError:
            return None
    node = levels.get(head)
    if node is None:
        return None
    if idx is not None:
        if not isinstance(node, list) or idx >= len(node):
            return None
        node = node[idx]
    if isinstance(node, dict):
        return node.get(rest) if rest else node.get("price")
    return node if not rest else None


def _anchored(levels: dict, basis, price) -> bool:
    """True when the quoted price really sits on the coordinate it claims."""
    coord = _coordinate(levels, basis)
    p = _f(price)
    if coord is None or p is None or not coord:
        return False
    return abs(p - float(coord)) / abs(float(coord)) <= ANCHOR_TOLERANCE


def _text(v, n: int) -> str:
    """Model prose, trimmed and un-escaped.

    The first real pass wrote "TIGER 미국S&amp;P500" into portfolio_note from a
    context that held a plain "&" — the model escaped it on its own. The card
    renders with textContent, so the entity would reach the reader verbatim.
    """
    return html.unescape(str(v or ""))[:n]


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int_qty(v):
    f = _f(v)
    if f is None or f != int(f) or int(f) <= 0:
        return None
    return int(f)


def _conditions(raw, symbol_levels: dict, basis: dict) -> tuple[dict, bool]:
    """Normalize the machine-evaluable predicates. Raises ValueError on a
    shape the keeper could not read. Returns (conditions, unanchored)."""
    raw = raw or {}
    if not isinstance(raw, dict):
        raise ValueError("conditions is not an object")
    out: dict = {}
    unanchored = False
    for key in _COND_KEYS:
        c = raw.get(key)
        if not c:
            continue
        if not isinstance(c, dict):
            raise ValueError(f"{key} is not an object")
        kind = c.get("kind")
        if kind not in _COND_KINDS:
            raise ValueError(f"{key}.kind {kind!r}")
        if kind in ("below", "above"):
            price = _f(c.get("price"))
            if price is None:
                raise ValueError(f"{key}.price missing")
            out[key] = {"kind": kind, "price": price}
            if not _anchored(symbol_levels, (basis or {}).get(key), price):
                unanchored = True
        else:
            out[key] = {"kind": kind}
    return out, unanchored


def _expires(raw, today: str):
    """'YYYY-MM-DD' in the future, else None — a plan that expired before it
    was written is not a plan."""
    if not raw:
        return None
    try:
        d = datetime.strptime(str(raw), "%Y-%m-%d").date()
    except ValueError:
        return None
    if d <= datetime.strptime(today, "%Y-%m-%d").date():
        return None
    return int(datetime(d.year, d.month, d.day, tzinfo=KST).timestamp())


def validate(doc: dict, ctx: dict) -> tuple[dict, list[dict]]:
    """Split the model's diff into what may be applied and what may not.

    Rejections are per item — one bad plan does not throw away the rest of the
    pass. The one exception raises IncompleteDiff: an active plan the model
    never mentioned, because a missing plan is indistinguishable from a kept
    one and this is the only place that can tell.
    """
    if not isinstance(doc, dict):
        raise IncompleteDiff("not an object")
    rejected: list[dict] = []
    today = ctx["account"]["date"]

    plans = {p["plan_id"]: p for p in ctx["plans"]}
    held = {h["symbol_id"]: h for h in ctx["holdings"]}
    watched = {w["symbol_id"] for w in ctx["watch"]}
    known_symbols = set(held) | watched
    planned_symbols = {p["symbol_id"] for p in ctx["plans"]}

    def _pid(item, key):
        raw = item if key == "keep" else (
            item.get("plan_id") if isinstance(item, dict) else None)
        try:
            return int(raw)
        except (TypeError, ValueError):
            rejected.append({"item": {key: item}, "why": "bad plan_id"})
            return None

    def _ids(key) -> list:
        return [pid for pid in ((_pid(i, key) for i in (doc.get(key) or [])))
                if pid is not None]

    seen: dict[int, str] = {}
    duplicated = []
    for key in ("keep", "modify", "abandon", "suggest"):
        for pid in _ids(key):
            if pid in seen:
                duplicated.append(pid)
            seen[pid] = key
    unjudged = sorted(set(plans) - set(seen))
    if unjudged or duplicated:
        raise IncompleteDiff(
            f"unjudged plans {unjudged}, judged twice {sorted(set(duplicated))}")

    accepted: dict = {"keep": [pid for pid in seen if seen[pid] == "keep"],
                      "modify": [], "abandon": [], "suggest": [], "add": [],
                      "reanalyze": []}

    def _own(pid, key, item) -> dict | None:
        plan = plans.get(pid)
        if plan is None:
            rejected.append({"item": {key: item}, "why": "no such active plan"})
            return None
        if plan.get("source") == "user":
            rejected.append({"item": {key: item},
                             "why": "a person's own plan is not the agent's to "
                                    "rewrite — say it in suggest"})
            return None
        return plan

    for item in doc.get("modify") or []:
        if not isinstance(item, dict):
            continue
        pid = _pid(item, "modify")
        plan = _own(pid, "modify", item) if pid is not None else None
        if plan is None:
            continue
        levels = _levels_of(ctx, plan["symbol_id"])
        try:
            # No `conditions` key at all means "leave them" — a modify that
            # only moves the quantity must not silently drop the stop.
            conds, unanchored = (
                _conditions(item["conditions"], levels, item.get("basis"))
                if "conditions" in item else (None, False))
        except ValueError as e:
            rejected.append({"item": {"modify": item}, "why": str(e)})
            continue
        qty = _int_qty(item.get("qty")) if item.get("qty") is not None else None
        if plan.get("side") == "sell" and qty:
            owned = _int_qty((held.get(plan["symbol_id"]) or {}).get("qty"))
            if owned is None:
                rejected.append({"item": {"modify": item},
                                 "why": "sell plan on an unheld symbol"})
                continue
            qty = min(qty, owned)
        accepted["modify"].append({
            "plan_id": pid, "symbol_id": plan["symbol_id"],
            "conditions": conds, "qty": qty,
            "expires_at": _expires(item.get("expires"), today),
            "why": _text(item.get("why"), 2000),
            "unanchored": unanchored})

    for item in doc.get("abandon") or []:
        if not isinstance(item, dict):
            continue
        pid = _pid(item, "abandon")
        if pid is None or _own(pid, "abandon", item) is None:
            continue
        accepted["abandon"].append(
            {"plan_id": pid, "why": _text(item.get("why"), 2000)})

    for item in doc.get("suggest") or []:
        if isinstance(item, dict) and item.get("plan_id") in plans:
            accepted["suggest"].append({
                "plan_id": item["plan_id"],
                "what": _text(item.get("what"), 500),
                "why": _text(item.get("why"), 2000)})

    # ── new plans, cheapest checks first, cash last ───────────────────────
    for item in doc.get("add") or []:
        if not isinstance(item, dict):
            continue
        sid = str(item.get("symbol_id") or "").upper()
        why = lambda w: rejected.append({"item": {"add": item}, "why": w})  # noqa: E731
        if sid not in known_symbols:
            why("symbol is neither held nor watched by this account")
            continue
        if sid in planned_symbols:
            why("this symbol already has an active plan — modify it")
            continue
        ana = ((held.get(sid) or next(
            (w for w in ctx["watch"] if w["symbol_id"] == sid), {}))
            .get("analysis")) or {}
        if ana.get("freshness") in (None, "missing"):
            why("no usable analysis for this symbol today")
            continue
        if item.get("side") not in ("buy", "sell"):
            why(f"bad side {item.get('side')!r}")
            continue
        qty = _int_qty(item.get("qty"))
        if qty is None:
            why("qty must be a whole number above zero")
            continue
        # `price` is the reference price the ledger stores, and an exit-only
        # plan has no obvious one to name — the model left it null on all 19
        # plans of the first real pass. It is derivable from the plan's own
        # numbers, so derive it rather than discard a complete plan.
        price = (_f(item.get("price"))
                 or _f(((item.get("conditions") or {}).get("entry") or {})
                       .get("price"))
                 or _f(((item.get("conditions") or {}).get("take_profit") or {})
                       .get("price"))
                 or _f((ana.get("levels") or {}).get("current")))
        if price is None or price <= 0:
            why("no price and none derivable from the conditions")
            continue
        if item["side"] == "sell":
            owned = _int_qty((held.get(sid) or {}).get("qty"))
            if owned is None:
                why("sell plan on an unheld symbol")
                continue
            qty = min(qty, owned)
        try:
            conds, unanchored = _conditions(item.get("conditions"),
                                            ana.get("levels") or {},
                                            item.get("basis"))
        except ValueError as e:
            why(str(e))
            continue
        accepted["add"].append({
            "symbol_id": sid,
            "name": _text(item.get("name") or _name_of(ctx, sid), 80),
            "side": item["side"], "qty": qty, "price": price,
            "currency": _currency_of(ctx, sid),
            "conditions": conds,
            "expires_at": _expires(item.get("expires"), today),
            "why": _text(item.get("why"), 2000),
            "unanchored": unanchored})

    # The prompt asks for the buys in descending confidence, so the ones that
    # do not fit come off the end. Cash is per currency: there is no rate in
    # this system and a KRW plan cannot be paid for with dollars.
    spend: dict[str, float] = {}
    kept_adds = []
    for a in accepted["add"]:
        if a["side"] != "buy":
            kept_adds.append(a)
            continue
        ccy = a["currency"]
        cash = (ctx["totals"].get(ccy) or {}).get("cash") or 0.0
        want = a["qty"] * a["price"]
        if spend.get(ccy, 0.0) + want > cash:
            rejected.append({"item": {"add": a},
                             "why": f"{ccy} cash {cash:,.0f} cannot cover this "
                                    f"and the buys above it"})
            continue
        spend[ccy] = spend.get(ccy, 0.0) + want
        kept_adds.append(a)
    accepted["add"] = kept_adds

    for item in doc.get("reanalyze") or []:
        if isinstance(item, dict) and item.get("symbol_id") in known_symbols:
            accepted["reanalyze"].append({
                "symbol_id": item["symbol_id"],
                "why": _text(item.get("why"), 500)})

    # ── one line per symbol; a card with nothing in it is worse than "none" ─
    notes = doc.get("notes_by_symbol")
    notes = notes if isinstance(notes, dict) else {}
    out_notes = {}
    for sid in sorted(known_symbols):
        n = notes.get(sid)
        if not isinstance(n, dict) or not n.get("line_ko"):
            out_notes[sid] = dict(_NO_NOTE)
            rejected.append({"item": {"notes_by_symbol": sid},
                             "why": "no line for this symbol"})
            continue
        stance = n.get("stance")
        out_notes[sid] = {
            "stance": stance if stance in STANCES else "none",
            "line_ko": _text(n["line_ko"], 400)}
    accepted["notes_by_symbol"] = out_notes
    accepted["portfolio_note"] = _text(doc.get("portfolio_note"), 2000)
    return accepted, rejected


def _name_of(ctx: dict, symbol_id: str) -> str:
    for row in ctx["holdings"] + ctx["watch"]:
        if row["symbol_id"] == symbol_id:
            return row.get("name") or symbol_id
    return symbol_id


def _currency_of(ctx: dict, symbol_id: str) -> str:
    for row in ctx["holdings"] + ctx["watch"]:
        if row["symbol_id"] == symbol_id:
            return row.get("currency") or ctx["account"]["base_currency"]
    return ctx["account"]["base_currency"]


# ── writing it down ───────────────────────────────────────────────────────

def apply(conn: sqlite3.Connection, account_id: str, accepted: dict, *,
          add_status: str, today: str) -> None:
    """One transaction. Either the whole pass lands or none of it does —
    a half-applied diff is a book nobody can reason about."""
    now = int(time.time())
    tag = f"[agent {today}]"
    conn.execute("BEGIN IMMEDIATE")
    try:
        for a in accepted["add"]:
            note = f"{tag} {a['why']}" + (" [unanchored]" if a["unanchored"]
                                          else "")
            conn.execute(
                "INSERT INTO plans (account_id, symbol_id, name, side, price,"
                " qty, note, status, source, conditions, expires_at,"
                " created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'agent', ?, ?, ?, ?)",
                (account_id, a["symbol_id"], a["name"], a["side"], a["price"],
                 a["qty"], note[:2000], add_status,
                 json.dumps(a["conditions"], ensure_ascii=False),
                 a["expires_at"], now, now))
        for m in accepted["modify"]:
            note = f"{tag} {m['why']}" + (" [unanchored]" if m["unanchored"]
                                          else "")
            conn.execute(
                "UPDATE plans SET conditions = COALESCE(?, conditions),"
                " qty = COALESCE(?, qty),"
                " expires_at = COALESCE(?, expires_at),"
                " note = TRIM(COALESCE(note, '') || char(10) || ?),"
                " updated_at = ?"
                " WHERE plan_id = ? AND account_id = ? AND source = 'agent'",
                (json.dumps(m["conditions"], ensure_ascii=False)
                 if m["conditions"] is not None else None,
                 m["qty"], m["expires_at"], note[:2000], now,
                 m["plan_id"], account_id))
        for a in accepted["abandon"]:
            conn.execute(
                "UPDATE plans SET status = 'abandoned',"
                " note = TRIM(COALESCE(note, '') || char(10) || ?),"
                " updated_at = ?"
                " WHERE plan_id = ? AND account_id = ? AND source = 'agent'",
                (f"{tag} {a['why']}"[:2000], now, a["plan_id"], account_id))
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _expire_proposals(conn: sqlite3.Connection, account_id: str,
                      today: str) -> int:
    cutoff = int((datetime.strptime(today, "%Y-%m-%d").replace(tzinfo=KST)
                  - timedelta(days=PROPOSED_TTL_DAYS)).timestamp())
    cur = conn.execute(
        "UPDATE plans SET status = 'expired', updated_at = ?"
        " WHERE account_id = ? AND status = 'proposed' AND created_at < ?",
        (int(time.time()), account_id, cutoff))
    conn.commit()
    return cur.rowcount


def _record(conn: sqlite3.Connection, account_id: str, *, day: str, slot: str,
            status: str, model: str, elapsed: float, ctx_chars: int = 0,
            diff=None, accepted=None, rejected=None) -> None:
    """A pass that failed still leaves a row. A silent failure is the one
    outcome the morning batch must not have."""
    j = (lambda o: json.dumps(o, ensure_ascii=False) if o is not None else None)
    conn.execute(
        "INSERT INTO plan_runs (account_id, date, slot, status, model,"
        " context_chars, diff, accepted, rejected, notes_by_symbol,"
        " portfolio_note, elapsed_s, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(account_id, date, slot) DO UPDATE SET"
        "  status=excluded.status, model=excluded.model,"
        "  context_chars=excluded.context_chars, diff=excluded.diff,"
        "  accepted=excluded.accepted, rejected=excluded.rejected,"
        "  notes_by_symbol=excluded.notes_by_symbol,"
        "  portfolio_note=excluded.portfolio_note,"
        "  elapsed_s=excluded.elapsed_s, created_at=excluded.created_at",
        (account_id, day, slot, status, model, ctx_chars, j(diff), j(accepted),
         j(rejected), j((accepted or {}).get("notes_by_symbol")),
         (accepted or {}).get("portfolio_note"), round(elapsed, 1),
         int(time.time())))
    conn.commit()


def run(conn: sqlite3.Connection, account_id: str, *, slot: str,
        model: str = SLOW_MODEL, dry: bool = False,
        today: str | None = None) -> dict:
    """One pass over one account. Never raises: every outcome is a row.

    `dry` writes the run but not the plans — the preview button. A `manual`
    slot always re-runs and replaces its row; `morning` is written once a day,
    so a re-run after a crash resumes rather than re-judging.
    """
    day = today or _kst_today()
    done = conn.execute(
        "SELECT status FROM plan_runs WHERE account_id = ? AND date = ?"
        "   AND slot = ?", (account_id, day, slot)).fetchone()
    if slot != "manual" and done and done[0] == "ok":
        return {"status": "skipped", "account_id": account_id, "date": day}

    if not dry:
        _expire_proposals(conn, account_id, day)

    ctx = context.build(conn, account_id, today=day)
    payload = USER_PLAN + context.as_json(ctx)
    out = {"status": None, "account_id": account_id, "date": day, "slot": slot}
    t0 = time.monotonic()

    accepted = rejected = doc = None
    status = "llm_error"
    for attempt in (1, 2):
        try:
            text = ClaudeCliBackend(model).call(SYSTEM_PLAN, payload)
        except Exception as e:  # noqa: BLE001 — a dead CLI is a run outcome
            out["error"] = str(e)[:200]
            status = "llm_error"
            break
        doc = extract_json(text)
        if doc is None:
            status = "parse_error"
            out["error"] = text[:200]
            continue
        try:
            accepted, rejected = validate(doc, ctx)
        except IncompleteDiff as e:
            status = "incomplete"
            out["error"] = str(e)[:200]
            continue
        status = "ok"
        break

    if status == "ok" and not dry:
        apply(conn, account_id, accepted, add_status="proposed", today=day)

    elapsed = time.monotonic() - t0
    _record(conn, account_id, day=day, slot=slot, status=status, model=model,
            elapsed=elapsed, ctx_chars=len(payload), diff=doc,
            accepted=accepted, rejected=rejected)
    out["status"] = status
    out["elapsed_s"] = round(elapsed, 1)
    if status == "ok":
        out.update(n_add=len(accepted["add"]), n_modify=len(accepted["modify"]),
                   n_abandon=len(accepted["abandon"]),
                   n_suggest=len(accepted["suggest"]),
                   n_rejected=len(rejected), applied=not dry)
    return out
