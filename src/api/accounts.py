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

import re

from typing import Dict, List, Optional

from fastapi import (
    APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response,
)
from pydantic import BaseModel

from ..portfolio import security, service
from ..portfolio.store import (account_store, balance_store, holding_store,
                               snapshot_store, trade_store)
from ..universe import search as uni_search

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


def require_account(account_id: str, request: Request) -> str:
    """Dependency guarding per-account routes. 404 if the account doesn't
    exist; 403 if the request lacks a valid session cookie. A book without a
    PIN is closed, not open — a PIN is set the moment the account is created,
    so "no PIN" only ever means a hand-edited row."""
    if not account_store.get(account_id):
        raise HTTPException(404, f"account not found: {account_id}")
    if not account_store.get_pin_hash(account_id):
        raise HTTPException(403, security.NO_PIN)
    token = request.cookies.get(security.COOKIE_PREFIX + account_id)
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


class PinChange(BaseModel):
    current: str
    pin: str


def _check_current_pin(account_id: str, pin: str) -> None:
    """The PIN again, for changes an open but unattended device must not make
    (PIN change, account deletion). Counted against the attempt limit."""
    ok, wait = security.attempt_pin(account_id, pin, account_store.get_pin_hash(account_id))
    if wait:
        raise HTTPException(429, f"PIN을 너무 많이 틀렸어요 · {wait // 60 + 1}분 뒤에 다시 해 주세요")
    if not ok:
        raise HTTPException(403, PIN_WRONG)


PIN_WRONG = "PIN이 맞지 않아요"
RESERVED_IDS = {"session", "new", "lock", "auth"}
ACCOUNT_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,40}")


@router.get("")
def list_accounts() -> dict:
    """Public list — identity only (name/type/broker), NO balances or holdings
    counts. Asset figures live behind the per-account PIN."""
    return {"accounts": [
        {"account_id": a["account_id"], "name": a["name"],
         "account_type": a["account_type"], "broker": a["broker"],
         "base_currency": a["base_currency"], "sort_order": a["sort_order"],
         "has_pin": bool(a.get("pin_hash"))}
        for a in account_store.list_all()]}


@router.get("/session")
def opened_accounts(request: Request) -> dict:
    """The accounts this device has open, with totals per currency — the
    account menu. Never a 403: a device with nothing open gets an empty list,
    and a locked account is simply absent (the menu shows its lock)."""
    out = []
    for a in account_store.list_all():
        aid = a["account_id"]
        if not account_store.get_pin_hash(aid):
            continue
        if not security.verify_session_token(aid, request.cookies.get(security.COOKIE_PREFIX + aid)):
            continue
        summary = service.aggregate(aid)
        out.append({"account_id": aid,
                    "totals": [{"currency": ccy, "total": t.get("total_assets")}
                               for ccy, t in summary.get("totals", {}).items()]})
    return {"open": out}


@router.post("")
def create_account(body: AccountCreate) -> dict:
    # an id that is also a path under /api/accounts would never open
    if body.account_id.lower() in RESERVED_IDS or not ACCOUNT_ID_RE.fullmatch(body.account_id):
        raise HTTPException(400, "쓸 수 없는 ID예요 · 영문·숫자·_·-만, 예약어 제외")
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
    if not account_store.get(account_id):
        raise HTTPException(404, f"account not found: {account_id}")
    stored = account_store.get_pin_hash(account_id)
    if not stored:
        raise HTTPException(403, security.NO_PIN)  # nothing to prove → nothing issued
    ok, wait = security.attempt_pin(account_id, body.pin, stored)
    if wait:
        raise HTTPException(429, f"PIN을 너무 많이 틀렸어요 · {wait // 60 + 1}분 뒤에 다시 해 주세요")
    if not ok:
        raise HTTPException(403, PIN_WRONG)
    security.set_session_cookie(response, security.COOKIE_PREFIX + account_id,
                                account_id)
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
def set_pin(account_id: str, body: PinChange,
            _: str = Depends(require_account)) -> dict:
    """Change the PIN — the current one again first: a device left open must
    not be enough to take the book over. Other devices stay open."""
    if not security.is_valid_pin_format(body.pin):
        raise HTTPException(400, "PIN must be exactly 6 digits")
    _check_current_pin(account_id, body.current)
    account_store.set_pin_hash(
        account_id, security.hash_pin(account_id, body.pin))
    return {"ok": True}


@router.delete("/{account_id}")
def delete_account(account_id: str, body: PinBody, response: Response,
                   _: str = Depends(require_account)) -> dict:
    """Deletes the account; its holdings, trades, cash and chats go with it
    (ON DELETE CASCADE), snapshots stay. Unchanged by the redesign — only the
    PIN is asked again first. The daily backup keeps seven days."""
    _check_current_pin(account_id, body.pin)
    n = account_store.delete(account_id)
    response.delete_cookie(security.COOKIE_PREFIX + account_id, path="/")
    return {"ok": n > 0, "deleted": n}


@router.post("/{account_id}/lock")
def lock_account(account_id: str, response: Response) -> dict:
    """이 기기에서 잠그기 — drop this device's session for the account. No
    session needed: clearing one's own cookie gives nothing away."""
    response.delete_cookie(security.COOKIE_PREFIX + account_id, path="/",
                           secure=True, httponly=True, samesite="lax")
    return {"ok": True}



# ── holdings ─────────────────────────────────────────────────────────

class HoldingUpsert(BaseModel):
    symbol_id: str
    create_only: bool = False               # the add dialog: an existing row is a 409
    avg_cost: Optional[float] = None
    qty: Optional[float] = None
    note: Optional[str] = None
    name: Optional[str] = None               # display-name override (종목명 교체)


def _universe_meta(symbol_id: str) -> dict:
    """The symbol's universe record — 400 for a malformed id, 404 for one the
    master does not know."""
    if ":" not in symbol_id:
        raise HTTPException(400, "symbol_id must be 'MARKET:CODE'")
    meta = uni_search.get(symbol_id)
    if meta is None:
        raise HTTPException(404, f"symbol not in universe: {symbol_id}")
    return meta


@router.post("/{account_id}/holdings")
def add_holding(account_id: str, body: HoldingUpsert,
                background: BackgroundTasks,
                _: str = Depends(require_account)) -> dict:
    """Add or directly edit a holding. Never touches cash — a trade goes
    through POST /trades, the one door for anything that moves money."""
    _universe_meta(body.symbol_id)
    if body.create_only and holding_store.exists(account_id, body.symbol_id):
        # POST /holdings writes what it is sent; the add dialog would overwrite
        # a held row's quantity with an empty one. Editing goes through 보유 정정.
        raise HTTPException(409, "이미 목록에 있는 종목이에요")
    backfill_bars, backfill_failed = service.fill_new_bars(body.symbol_id)
    holding_store.upsert(
        account_id, body.symbol_id,
        avg_cost=body.avg_cost, qty=body.qty, note=body.note, name=body.name,
    )
    background.add_task(service.map_new_themes, body.symbol_id)
    return {"ok": True, "backfill_bars": backfill_bars,
            "backfill_failed": backfill_failed}


@router.delete("/{account_id}/holdings/{symbol_id:path}")
def remove_holding(account_id: str, symbol_id: str,
                   _: str = Depends(require_account)) -> dict:
    """Delete only. A sellout is recorded through POST /trades first. The
    symbol's theme mapping (market.db) stays: it is a fact about the symbol,
    and another account may still hold it (ops notes §4)."""
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
# holdings (qty · weighted avg) and cash (§3.1).

class TradeBody(BaseModel):
    symbol_id: str
    side: str                                # 'buy' | 'sell'
    qty: float
    price: float
    fee: float = 0.0
    traded_at: Optional[int] = None          # epoch seconds; default now
    note: Optional[str] = None


@router.post("/{account_id}/trades")
def record_trade(account_id: str, body: TradeBody,
                 background: BackgroundTasks,
                 _: str = Depends(require_account)) -> dict:
    symbol_id = (body.symbol_id or "").strip()
    meta = _universe_meta(symbol_id)
    side = (body.side or "").lower()
    try:
        out = trade_store.record(
            account_id, symbol_id=symbol_id, side=side,
            qty=body.qty, price=body.price, fee=body.fee,
            currency=uni_search.currency_of(symbol_id, meta),
            name=meta.get("name_kr") or meta.get("name_en") or symbol_id,
            traded_at=body.traded_at,
            note=(body.note or "").strip() or None)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if side == "buy" and out["holding"]["qty"] == body.qty:
        # a freshly opened position: its bars and themes, off the request path
        background.add_task(service.after_new_holding, symbol_id)
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


