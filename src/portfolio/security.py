"""Account PIN hashing + session-token signing.

Pure crypto helpers — no DB, no FastAPI. The server secret lives in a
file under data/ (Oracle-only, gitignored) so it survives restarts but is
never committed. Delete that file to invalidate every session + force PIN
re-hash (PINs would need re-setting since the hash is secret-salted).

PINs are 6-digit numeric. The server secret acts as a salt so a leaked DB
alone can't be brute-forced with a rainbow table; an attacker would also
need the secret file.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import threading
import time

from ..core.paths import DATA

_SECRET_FILE = DATA / ".session_secret"
_secret_cache: bytes | None = None

COOKIE_PREFIX = "pdash_a_"
SESSION_TTL = 30 * 86400   # 30 days — cookie lifetime and token expiry
NO_PIN = "PIN을 먼저 설정하세요"   # a book without a PIN is closed, not open


def _secret() -> bytes:
    global _secret_cache
    if _secret_cache is not None:
        return _secret_cache
    if _SECRET_FILE.exists():
        _secret_cache = _SECRET_FILE.read_bytes()
        return _secret_cache
    _SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    s = secrets.token_bytes(32)
    _SECRET_FILE.write_bytes(s)
    try:
        os.chmod(_SECRET_FILE, 0o600)
    except OSError:
        pass
    _secret_cache = s
    return s


def hash_pin(account_id: str, pin: str) -> str:
    """Secret-salted hash for storage. Same (account_id, pin) → same hash,
    so we can verify by recomputing and comparing."""
    msg = b":".join([_secret(), account_id.encode(), pin.encode()])
    return hashlib.sha256(msg).hexdigest()


def verify_pin(account_id: str, pin: str, stored_hash: str | None) -> bool:
    if not stored_hash:
        return False
    return hmac.compare_digest(hash_pin(account_id, pin), stored_hash)


def make_session_token(account_id: str) -> str:
    """Signed token proving the holder authenticated this account.
    Format: '{account_id}|{issued_ts}|{hmac_sig}', valid for SESSION_TTL."""
    payload = f"{account_id}|{int(time.time())}"
    sig = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}|{sig}"


def chat_binding(account_id: str) -> str:
    """Signature binding a chat agent's tool process to one account.

    The server hands the tool the account and this signature through the
    environment; the tool refuses unless they match. The CLI already refuses
    a command the agent prefixes with its own variables, so this is the
    second wall: an agent that got past that check still cannot sign another
    account. The NUL keeps it apart from session tokens ('{id}|{ts}')."""
    return hmac.new(_secret(), f"chat-tool\x00{account_id}".encode(),
                    hashlib.sha256).hexdigest()


def set_session_cookie(response, name: str, account_id: str) -> None:
    """Issue the session cookie `name` for `account_id` on `response`."""
    response.set_cookie(name, make_session_token(account_id),
                        httponly=True, samesite="lax", secure=True, path="/",
                        max_age=SESSION_TTL)


def verify_session_token(account_id: str, token: str | None) -> bool:
    if not token:
        return False
    parts = token.split("|")
    if len(parts) != 3:
        return False
    tok_acct, issued, sig = parts
    if tok_acct != account_id:
        return False
    expect = hmac.new(_secret(), f"{tok_acct}|{issued}".encode(),
                      hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expect):
        return False
    try:
        return time.time() - int(issued) <= SESSION_TTL
    except ValueError:
        return False


def has_any_session(cookies) -> bool:
    """True if any pdash_a_* cookie carries a valid token. Used by the global
    /api gate: proving one account unlocks the shared market/analysis data."""
    return any(
        verify_session_token(name[len(COOKIE_PREFIX):], value)
        for name, value in cookies.items() if name.startswith(COOKIE_PREFIX)
    )


def is_valid_pin_format(pin: str) -> bool:
    return isinstance(pin, str) and len(pin) == 6 and pin.isdigit()


# ── PIN attempts ─────────────────────────────────────────────────────────────
# A 6-digit PIN is a million guesses; without a limit a script walks it in an
# afternoon. Per account, in memory: MAX_TRIES wrong PINs lock that account's
# PIN checks for LOCK_S. One web process serves the site, so memory is the
# whole state; a restart forgets the count, which costs an attacker a restart
# they cannot trigger.
MAX_TRIES = 5
LOCK_S = 15 * 60
_fails: dict[str, list[float]] = {}
_fails_lock = threading.Lock()


def attempt_pin(account_id: str, pin: str, stored_hash: str | None,
                now: float | None = None) -> tuple[bool, int]:
    """(matched, seconds locked). The check and the count happen under one
    lock: sixty concurrent wrong PINs let 25 through when they were apart."""
    with _fails_lock:
        wait = pin_locked_for(account_id, now)
        if wait:
            return False, wait
        return check_pin(account_id, pin, stored_hash, now), 0


def pin_locked_for(account_id: str, now: float | None = None) -> int:
    """Seconds until this account's PIN may be tried again (0 = now)."""
    now = time.time() if now is None else now
    recent = [t for t in _fails.get(account_id, []) if now - t < LOCK_S]
    _fails[account_id] = recent
    return int(LOCK_S - (now - recent[-MAX_TRIES])) + 1 if len(recent) >= MAX_TRIES else 0


def check_pin(account_id: str, pin: str, stored_hash: str | None,
              now: float | None = None) -> bool:
    """verify_pin that counts failures; a success clears them. The caller
    checks pin_locked_for first."""
    now = time.time() if now is None else now
    if verify_pin(account_id, pin, stored_hash):
        _fails.pop(account_id, None)
        return True
    _fails.setdefault(account_id, []).append(now)
    return False


def _reset_pin_cli(account_id: str) -> int:
    """`python -m src.portfolio.security reset-pin ACCOUNT` on the server —
    the procedure '계좌 주인에게 재설정을 부탁해요' points to. Every session of
    that account keeps working (tokens are not PIN-bound); each device can
    lock itself from the page, and rotating data/.session_secret closes every
    session of every account."""
    import getpass
    from .store import account_store
    if not account_store.get(account_id):
        print(f"no such account: {account_id}")
        return 1
    pin = getpass.getpass("새 PIN 6자리: ")
    if not is_valid_pin_format(pin) or getpass.getpass("한 번 더: ") != pin:
        print("PIN이 6자리 숫자가 아니거나 두 번이 달라요")
        return 1
    account_store.set_pin_hash(account_id, hash_pin(account_id, pin))
    _fails.pop(account_id, None)
    print(f"{account_id}: PIN을 바꿨어요")
    return 0


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == "reset-pin":
        sys.exit(_reset_pin_cli(sys.argv[2]))
    print("usage: python -m src.portfolio.security reset-pin ACCOUNT")
    sys.exit(2)
