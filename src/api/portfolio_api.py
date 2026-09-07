"""/api/accounts — account-aware portfolio CRUD + per-account PIN auth.

Auth model: every account has a 6-digit PIN (pin_hash); creating one requires
it. Hitting a protected route requires a valid session cookie (pdash_a_{id});
you get one by POSTing the PIN to /api/accounts/{id}/auth. The cookie lives
for security.SESSION_TTL. A book without a PIN is closed (403, no cookie) —
never "open until assigned".

Two gates stack. The server-wide /api middleware (dashboard_web.server) opens
only the account list, account creation and POST /{id}/auth without a
session; `require_account` then narrows a per-account route to THAT
account's session.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import (
    APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response,
)
from pydantic import BaseModel

from ..core import security
from ..research.news.mapping import ensure_theme_mapping
from ..market.pipeline import gap_filler
from ..ledger import planner
from ..portfolio import service
from ..portfolio.store import (
    account_store, balance_store, db, holding_store, plan_run_store,
    plan_store, snapshot_store, trade_store,
)
from ..research.llm_backend import SLOW_MODEL
from . import analysis_jobs
from ..core.paths import split_symbol
from ..universe import search as uni_search

router = APIRouter(prefix="/api/accounts", tags=["accounts"])

_COOKIE_PREFIX = security.COOKIE_PREFIX


_NO_PIN = security.NO_PIN


def require_account(account_id: str, request: Request) -> str:
    """Dependency guarding per-account routes. 404 if the account doesn't
    exist; 403 if the request lacks a valid session cookie. A book without a
    PIN is closed, not open — a PIN is set the moment the account is created,
    so "no PIN" only ever means a hand-edited row."""
    if not account_store.get(account_id):
        raise HTTPException(404, f"account not found: {account_id}")
    if not account_store.get_pin_hash(account_id):
        raise HTTPException(403, _NO_PIN)
    token = request.cookies.get(_COOKIE_PREFIX + account_id)
    if not security.verify_session_token(account_id, token):
        # 403, never 401 — the PIN gate is the app's own, and a 401 turns it
        # into an HTTP auth challenge the browser answers with a login popup.
        raise HTTPException(403, "PIN required")
    return account_id


# ── accounts ─────────────────────────────────────────────────────────

class AccountCreate(BaseModel):
    account_id: str
    name: str
    account_type: str = "GENERAL"
    broker: Optional[str] = None
    base_currency: str = "KRW"
    note: Optional[str] = None
    pin: str                         # 6-digit; set once at creation — a book is never open


class AccountUpdate(BaseModel):
    name: Optional[str] = None
    account_type: Optional[str] = None
    broker: Optional[str] = None
    base_currency: Optional[str] = None
    note: Optional[str] = None


class PinBody(BaseModel):
    pin: str


@router.get("")
def list_accounts() -> dict:
    """Public list — identity only (name/type/broker), NO balances or holdings
    counts. Asset figures live behind the per-account PIN."""
    out = []
    for a in account_store.list_all():
        out.append({
            "account_id": a["account_id"],
            "name": a["name"],
            "account_type": a["account_type"],
            "broker": a["broker"],
            "base_currency": a["base_currency"],
            "sort_order": a["sort_order"],
            "has_pin": bool(a.get("pin_hash")),
        })
    return {"accounts": out}


@router.post("")
def create_account(body: AccountCreate) -> dict:
    if account_store.get(body.account_id):
        raise HTTPException(409, f"account exists: {body.account_id}")
    if not security.is_valid_pin_format(body.pin):
        raise HTTPException(400, "PIN must be exactly 6 digits")
    account_store.create(
        body.account_id, body.name, body.account_type,
        broker=body.broker, base_currency=body.base_currency, note=body.note,
    )
    account_store.set_pin_hash(
        body.account_id, security.hash_pin(body.account_id, body.pin))
    return {"ok": True, "account_id": body.account_id}


@router.post("/{account_id}/auth")
def auth_account(account_id: str, body: PinBody, response: Response) -> dict:
    acct = account_store.get(account_id)
    if not acct:
        raise HTTPException(404, f"account not found: {account_id}")
    stored = account_store.get_pin_hash(account_id)
    if not stored:
        raise HTTPException(403, _NO_PIN)      # nothing to prove → nothing issued
    if not security.verify_pin(account_id, body.pin, stored):
        raise HTTPException(403, "PIN이 일치하지 않습니다")
    security.set_session_cookie(response, _COOKIE_PREFIX + account_id, account_id)
    return {"ok": True}


@router.get("/{account_id}")
def get_account(account_id: str, _: str = Depends(require_account)) -> dict:
    summary = service.aggregate(account_id)
    if not summary.get("exists"):
        raise HTTPException(404, f"account not found: {account_id}")
    # surface whether a PIN is set so the UI can offer "PIN 변경/설정"
    summary["has_pin"] = bool(account_store.get_pin_hash(account_id))
    return summary


@router.patch("/{account_id}")
def update_account(account_id: str, body: AccountUpdate,
                   _: str = Depends(require_account)) -> dict:
    n = account_store.update(
        account_id, name=body.name, account_type=body.account_type,
        broker=body.broker, base_currency=body.base_currency, note=body.note,
    )
    if n == 0:
        raise HTTPException(404, f"account not found: {account_id}")
    return {"ok": True, "account": account_store.get(account_id)}


@router.post("/{account_id}/pin")
def set_pin(account_id: str, body: PinBody,
            _: str = Depends(require_account)) -> dict:
    """Change/set the PIN of an already-authenticated account."""
    if not security.is_valid_pin_format(body.pin):
        raise HTTPException(400, "PIN must be exactly 6 digits")
    account_store.set_pin_hash(
        account_id, security.hash_pin(account_id, body.pin))
    return {"ok": True}


@router.delete("/{account_id}")
def delete_account(account_id: str, _: str = Depends(require_account)) -> dict:
    n = account_store.delete(account_id)
    return {"ok": n > 0, "deleted": n}


# ── holdings ─────────────────────────────────────────────────────────

class HoldingUpsert(BaseModel):
    symbol_id: str
    avg_cost: Optional[float] = None
    qty: Optional[float] = None
    note: Optional[str] = None
    name: Optional[str] = None               # display-name override (종목명 교체)


def _currency_of(symbol_id: str) -> str:
    """Resolve the cash currency a symbol settles in. Prefer the universe
    record; fall back to the symbol_id region prefix (KR→KRW, US→USD)."""
    meta = uni_search.get(symbol_id) or {}
    ccy = meta.get("currency")
    if ccy:
        return str(ccy).upper()
    region, _ = split_symbol(symbol_id)
    return "USD" if region == "US" else "KRW"


@router.post("/{account_id}/holdings")
def add_holding(account_id: str, body: HoldingUpsert,
                background: BackgroundTasks,
                _: str = Depends(require_account)) -> dict:
    """Add or directly edit a holding. Never touches cash — a trade goes
    through POST /trades, the one door for anything that moves money."""
    if ":" not in body.symbol_id:
        raise HTTPException(400, "symbol_id must be 'MARKET:CODE'")
    if uni_search.get(body.symbol_id) is None:
        raise HTTPException(404, f"symbol not in universe: {body.symbol_id}")
    backfill_bars = gap_filler.fill_one(body.symbol_id)
    holding_store.upsert(
        account_id, body.symbol_id,
        avg_cost=body.avg_cost, qty=body.qty, note=body.note, name=body.name,
    )
    # Map the symbol into themes off the request path (may make an LLM call).
    background.add_task(ensure_theme_mapping, body.symbol_id)
    return {"ok": True, "backfill_bars": backfill_bars}


@router.delete("/{account_id}/holdings/{symbol_id:path}")
def remove_holding(account_id: str, symbol_id: str,
                   _: str = Depends(require_account)) -> dict:
    """Delete only. A sellout is recorded through POST /trades first. The
    symbol's theme mapping (market.db) stays: it is a fact about the symbol,
    and the other book may still hold it (ops notes §4)."""
    n = holding_store.remove(account_id, symbol_id)
    return {"ok": n > 0, "deleted": n}


class HoldingsReorder(BaseModel):
    order: List[str]


@router.post("/{account_id}/holdings/reorder")
def reorder_holdings(account_id: str, body: HoldingsReorder,
                     _: str = Depends(require_account)) -> dict:
    return {"ok": True,
            "reordered": holding_store.reorder(account_id, body.order)}


# ── trades (거래 기록) ────────────────────────────────────────────────
# The one door for money: writes the trade row and, in the same transaction,
# holdings (qty · weighted avg), cash and the linked plan's status (§3.1).

class TradeBody(BaseModel):
    symbol_id: str
    side: str                                # 'buy' | 'sell'
    qty: float
    price: float
    fee: float = 0.0
    traded_at: Optional[int] = None          # epoch seconds; default now
    plan_id: Optional[int] = None
    note: Optional[str] = None


@router.post("/{account_id}/trades")
def record_trade(account_id: str, body: TradeBody,
                 background: BackgroundTasks,
                 _: str = Depends(require_account)) -> dict:
    symbol_id = (body.symbol_id or "").strip()
    if ":" not in symbol_id:
        raise HTTPException(400, "symbol_id must be 'MARKET:CODE'")
    meta = uni_search.get(symbol_id)
    if meta is None:
        raise HTTPException(404, f"symbol not in universe: {symbol_id}")
    side = (body.side or "").lower()
    try:
        out = trade_store.record(
            account_id, symbol_id=symbol_id, side=side,
            qty=body.qty, price=body.price, fee=body.fee,
            currency=_currency_of(symbol_id),
            name=meta.get("name_kr") or meta.get("name_en") or symbol_id,
            traded_at=body.traded_at, plan_id=body.plan_id,
            note=(body.note or "").strip() or None)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    if side == "buy" and out["holding"]["qty"] == body.qty:
        # a freshly opened position: same background work as POST /holdings
        background.add_task(gap_filler.fill_one, symbol_id)
        background.add_task(ensure_theme_mapping, symbol_id)
    out["ok"] = True
    return out


@router.get("/{account_id}/trades")
def list_trades(account_id: str, limit: int = 200,
                symbol_id: Optional[str] = None,
                since: Optional[str] = None, until: Optional[str] = None,
                _: str = Depends(require_account)) -> dict:
    """Newest first. `since`/`until` are inclusive KST days (YYYY-MM-DD)."""
    try:
        rows = trade_store.list_by_account(
            account_id, max(1, min(limit, 1000)),
            symbol_id=(symbol_id or "").strip() or None,
            since=since or None, until=until or None)
    except ValueError:
        raise HTTPException(400, "since/until must be YYYY-MM-DD")
    return {"account_id": account_id, "trades": rows}


# ── balances ─────────────────────────────────────────────────────────

class BalancesBody(BaseModel):
    balances: Dict[str, float]


@router.get("/{account_id}/balances")
def get_balances(account_id: str,
                 _: str = Depends(require_account)) -> dict:
    return {"account_id": account_id,
            "balances": balance_store.get_all(account_id)}


@router.post("/{account_id}/balances")
def set_balances(account_id: str, body: BalancesBody,
                 _: str = Depends(require_account)) -> dict:
    n = balance_store.set_many(account_id, body.balances)
    return {"ok": True, "updated": n,
            "balances": balance_store.get_all(account_id)}


# ── snapshots ────────────────────────────────────────────────────────

@router.get("/{account_id}/snapshots")
def get_snapshots(account_id: str, currency: str = "KRW", days: int = 90,
                  _: str = Depends(require_account)) -> dict:
    return {"account_id": account_id, "currency": currency, "days": days,
            "snapshots": snapshot_store.read(account_id, currency, days)}


# ── plans (매매 계획 노트) ────────────────────────────────────────────
# Intent only. Nothing here writes to holdings — an executed plan is recorded
# through POST /trades, which is what moves plan.status (§3.1).

class PlanBody(BaseModel):
    symbol_id: str
    name: Optional[str] = None
    side: str
    price: float
    qty: float
    note: Optional[str] = None


class PlanPatch(BaseModel):
    symbol_id: Optional[str] = None
    name: Optional[str] = None
    side: Optional[str] = None
    price: Optional[float] = None
    qty: Optional[float] = None
    note: Optional[str] = None
    # The person's own exits: a trade row moves armed → entered → done, but
    # "changed my mind" (abandoned) and a close the trade could not attribute
    # (two plans matched) have no trade to ride on.
    status: Optional[str] = None


def _clean_plan(side: Optional[str], price: Optional[float],
                qty: Optional[float]) -> None:
    if side is not None and side not in plan_store.SIDES:
        raise HTTPException(400, "side must be 'buy' or 'sell'")
    if price is not None and price <= 0:
        raise HTTPException(400, "price must be > 0")
    if qty is not None and qty <= 0:
        raise HTTPException(400, "qty must be > 0")


@router.get("/{account_id}/plans")
def list_plans(account_id: str, _: str = Depends(require_account)) -> dict:
    return {"account_id": account_id, "plans": plan_store.list_all(account_id)}


@router.post("/{account_id}/plans")
def create_plan(account_id: str, body: PlanBody,
                _: str = Depends(require_account)) -> dict:
    _clean_plan(body.side, body.price, body.qty)
    symbol_id = (body.symbol_id or "").strip()
    if not symbol_id:
        raise HTTPException(400, "symbol_id is required")
    name = (body.name or "").strip()
    if not name:
        rec = uni_search.get(symbol_id) or {}
        name = rec.get("name_kr") or rec.get("name_en") or symbol_id
    plan = plan_store.create(account_id, symbol_id=symbol_id, name=name,
                             side=body.side, price=body.price, qty=body.qty,
                             note=(body.note or "").strip())
    return {"ok": True, "plan": plan}


@router.patch("/{account_id}/plans/{plan_id}")
def update_plan(account_id: str, plan_id: int, body: PlanPatch,
                _: str = Depends(require_account)) -> dict:
    _clean_plan(body.side, body.price, body.qty)
    if body.status is not None and body.status not in plan_store.HUMAN_STATUSES:
        raise HTTPException(400, "status must be one of "
                            + ", ".join(plan_store.HUMAN_STATUSES))
    if body.status == "proposed":
        # A proposal is something an agent wrote and a person has not answered
        # yet. Letting anything become one would put an approved plan back in
        # the queue and hide it from the executor.
        raise HTTPException(400, "proposed is not a status anything moves into")
    current = plan_store.get(account_id, plan_id)
    if current is None:
        raise HTTPException(404, "plan not found")
    if current["status"] == "proposed":
        # Approving or rejecting is the only edit a proposal takes: it is the
        # agent's text, and a person who wants different numbers writes their
        # own plan.
        if body.status not in ("armed", "abandoned"):
            raise HTTPException(400, "a proposal is approved (armed) or "
                                     "rejected (abandoned)")
        plan = plan_store.decide(account_id, plan_id, body.status)
        if plan is None:
            raise HTTPException(404, "plan not found")
        return {"ok": True, "plan": plan}
    plan = plan_store.update(account_id, plan_id, **body.model_dump())
    if plan is None:
        raise HTTPException(404, "plan not found")
    return {"ok": True, "plan": plan}


# ── plan_runs (계좌별 계획 판단) ──────────────────────────────────────
# Declaration order matters: FastAPI matches top-down, so the literal paths
# have to be declared before /{date} or "latest" is read as a date.

@router.get("/{account_id}/plan_runs/latest")
def latest_plan_run(account_id: str,
                    _: str = Depends(require_account)) -> dict:
    """The newest pass that produced something.

    "Nothing yet" is an answer, not a 404 — the card grid asks this on every
    load and a missing pass is the normal state of a new account.
    """
    return plan_run_store.latest(account_id) or {
        "date": None, "slot": None, "status": None, "portfolio_note": "",
        "notes_by_symbol": {}, "suggest": [], "n_rejected": 0,
        "created_at": None}


@router.post("/{account_id}/plan_runs/run")
def run_plan_preview(account_id: str,
                     _: str = Depends(require_account)) -> dict:
    """A preview. Always dry: this writes a plan_runs row and nothing else.

    The morning cron is what applies a pass. A button that writes to the book
    would need its own PIN re-check and its own duplicate guard, and nothing
    yet asks for one.
    """
    def _fn() -> dict:
        with db.connect() as conn:
            return planner.run(conn, account_id, slot="manual",
                               model=SLOW_MODEL, dry=True)

    job = analysis_jobs.start(_job_key(account_id), _fn)
    return {"ok": True, "account_id": account_id, "job": job}


@router.get("/{account_id}/plan_runs/status")
def plan_preview_status(account_id: str,
                        _: str = Depends(require_account)) -> dict:
    return {"account_id": account_id,
            **analysis_jobs.get_status(_job_key(account_id))}


@router.get("/{account_id}/plan_runs")
def list_plan_runs(account_id: str, limit: int = 30,
                   _: str = Depends(require_account)) -> dict:
    return {"account_id": account_id,
            "dates": plan_run_store.dates(account_id, limit=max(1, min(limit, 90)))}


@router.get("/{account_id}/plan_runs/{date}")
def plan_run_on(account_id: str, date: str,
                _: str = Depends(require_account)) -> dict:
    got = plan_run_store.on(account_id, date)
    if got is None:
        raise HTTPException(404, "no pass on that date")
    return got


def _job_key(account_id: str) -> str:
    """The preview shares the analysis job registry, which is keyed by symbol
    id — the prefix is what keeps an account from colliding with one."""
    return f"plan:{account_id}"


@router.delete("/{account_id}/plans/{plan_id}")
def delete_plan(account_id: str, plan_id: int,
                _: str = Depends(require_account)) -> dict:
    if not plan_store.delete(account_id, plan_id):
        raise HTTPException(404, "plan not found")
    return {"ok": True, "deleted": plan_id}
