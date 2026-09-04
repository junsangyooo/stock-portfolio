"""KIS OpenAPI auth + shared request plumbing.

Sole remaining consumer: `us_screener.screen` (US 조건검색), which Toss has no
equivalent for. Trading, balances and quotes moved to `toss.py`; the KIS
account is kept alive for the screener alone.

Credentials come from the environment (systemd EnvironmentFile / cron sourcing
~/dotclaude/secrets/.env), falling back to parsing that file directly for
local CLI runs — same pattern as dart_source. Names match what already lives
in .env:

  KIS_APP_KEY / KIS_APP_SECRET
  KIS_ACCOUNT_NO              first 8 digits  -> CANO
  KIS_ACCOUNT_PRODUCT_CODE    2 digits        -> ACNT_PRDT_CD
  KIS_IS_REAL                 "1"/"true" = real server, else paper

Token: POST /oauth2/tokenP, valid 24h, and KIS re-issues at most once per
minute — so the token is cached in data/quant/.kis_token.json and reused
until 1h before expiry.

Rate limit: KIS documents only "excessive requests may be blocked"; the
commonly cited ceiling is 20 req/s (real) which we have not verified, so the
token bucket here is a deliberately conservative 8 req/s.
# ponytail: fixed 8/s bucket; raise after measuring the real ceiling.

Never print credentials. The old quant_investment auth.py dumped appkey and
secret to stdout on every call (_DEBUG=True) — that behaviour must not return.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from typing import Optional

import requests

from ...core import secrets
from ...core.paths import QUANT_DIR
from ...core.ratelimit import TokenBucket

REAL_URL = "https://openapi.koreainvestment.com:9443"
PAPER_URL = "https://openapivts.koreainvestment.com:29443"

_TOKEN_PATH = QUANT_DIR / ".kis_token.json"
_REFRESH_MARGIN_S = 3600          # renew when less than 1h of validity remains

_RATE = 8.0                       # requests per second
_BURST = 8.0


class KisError(RuntimeError):
    """Transport-level or rt_cd != '0' failure. .payload holds the raw body."""

    def __init__(self, msg: str, payload: Optional[dict] = None):
        super().__init__(msg)
        self.payload = payload or {}


def creds() -> dict:
    c = {
        "app_key": secrets.get("KIS_APP_KEY"),
        "app_secret": secrets.get("KIS_APP_SECRET"),
        "cano": secrets.get("KIS_ACCOUNT_NO"),
        "prdt": secrets.get("KIS_ACCOUNT_PRODUCT_CODE") or "01",
        "real": (secrets.get("KIS_IS_REAL") or "").lower() in ("1", "true", "y", "yes"),
    }
    missing = [k for k in ("app_key", "app_secret", "cano") if not c[k]]
    if missing:
        raise KisError(f"missing KIS env: {', '.join('KIS_' + m.upper() for m in missing)}")
    c["base"] = REAL_URL if c["real"] else PAPER_URL
    return c


# ── token ────────────────────────────────────────────────────────────────

_tok_lock = threading.Lock()


def _load_cached_token() -> Optional[str]:
    try:
        d = json.loads(_TOKEN_PATH.read_text(encoding="utf-8"))
        if d.get("expires_at", 0) - time.time() > _REFRESH_MARGIN_S:
            return d.get("token")
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return None


def token(force: bool = False) -> str:
    with _tok_lock:
        if not force:
            t = _load_cached_token()
            if t:
                return t
        c = creds()
        res = requests.post(
            f"{c['base']}/oauth2/tokenP",
            json={"grant_type": "client_credentials",
                  "appkey": c["app_key"], "appsecret": c["app_secret"]},
            timeout=15)
        body = res.json()
        if res.status_code != 200 or "access_token" not in body:
            raise KisError(f"token issue failed: HTTP {res.status_code} "
                           f"{body.get('error_code', '')} {body.get('error_description', '')}",
                           body)
        tok = body["access_token"]
        # access_token_token_expired: 'YYYY-MM-DD HH:MM:SS' (KST server clock)
        try:
            exp = datetime.strptime(body["access_token_token_expired"],
                                    "%Y-%m-%d %H:%M:%S").timestamp()
        except (KeyError, ValueError):
            exp = time.time() + 23 * 3600
        QUANT_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        # owner-only, written atomically — the file holds a live trading token
        tmp = _TOKEN_PATH.with_suffix(".tmp")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"token": tok, "expires_at": exp}, f)
        os.replace(tmp, _TOKEN_PATH)
        return tok


# ── rate limiter ─────────────────────────────────────────────────────────

_bucket = TokenBucket(_RATE, _BURST)


# ── request ──────────────────────────────────────────────────────────────

def call(path: str, tr_id: str, *, params: Optional[dict] = None,
         body: Optional[dict] = None, tr_cont: str = "") -> dict:
    """One KIS request. GET when body is None, POST otherwise.

    Returns the parsed JSON body when rt_cd == '0'; raises KisError otherwise
    (msg_cd/msg1 in the message). Retries exactly once on the expired-token
    error by force-refreshing.
    """
    c = creds()
    for attempt in (1, 2):
        _bucket.take()
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {token(force=attempt == 2)}",
            "appkey": c["app_key"],
            "appsecret": c["app_secret"],
            "tr_id": tr_id,
            "custtype": "P",
            "tr_cont": tr_cont,
        }
        url = c["base"] + path
        try:
            if body is None:
                res = requests.get(url, headers=headers, params=params or {},
                                   timeout=15)
            else:
                res = requests.post(url, headers=headers, data=json.dumps(body),
                                    timeout=15)
        except requests.RequestException as e:
            raise KisError(f"KIS request failed: {e}") from e
        try:
            data = res.json()
        except ValueError:
            raise KisError(f"KIS non-JSON response HTTP {res.status_code}")
        if data.get("rt_cd") == "0":
            return data
        # EGW00123: token expired / invalid — refresh once and retry
        if attempt == 1 and data.get("msg_cd") in ("EGW00123", "EGW00121"):
            continue
        raise KisError(
            f"KIS {tr_id} error: {data.get('msg_cd', res.status_code)} "
            f"{(data.get('msg1') or '').strip()}", data)
    raise KisError("unreachable")  # pragma: no cover
