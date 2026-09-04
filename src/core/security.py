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
import time

from .paths import DATA

_SECRET_FILE = DATA / ".session_secret"
_secret_cache: bytes | None = None

COOKIE_PREFIX = "pdash_a_"
SESSION_TTL = 30 * 86400   # 30 days — cookie lifetime and token expiry


def _secret() -> bytes:
    global _secret_cache
    if _secret_cache is not None:
        return _secret_cache
    if _SECRET_FILE.exists():
        _secret_cache = _SECRET_FILE.read_bytes()
        return _secret_cache
    DATA.mkdir(parents=True, exist_ok=True)
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


# quant's own cookie. Not the portfolio prefix: different logins on different
# origins, and sharing the prefix would make one look like the other. Defined
# here so server.py can name it without importing the quant package.
QUANT_COOKIE = "pdash_q"


def token_is_valid(token: str | None) -> bool:
    """Self-describing check: the account id is inside the token and the
    signature covers it, so no lookup is needed to trust it. This is how the
    global /api gate accepts a quant session without opening quant.db."""
    if not token:
        return False
    return verify_session_token(token.split("|", 1)[0], token)


def has_any_session(cookies) -> bool:
    """True if any pdash_a_* cookie carries a valid token. Used by the global
    /api gate: proving one account unlocks the shared market/analysis data."""
    return any(
        verify_session_token(name[len(COOKIE_PREFIX):], value)
        for name, value in cookies.items() if name.startswith(COOKIE_PREFIX)
    )


def is_valid_pin_format(pin: str) -> bool:
    return isinstance(pin, str) and len(pin) == 6 and pin.isdigit()
