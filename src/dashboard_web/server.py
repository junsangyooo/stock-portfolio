"""FastAPI dashboard server for portfolio-dashboard.

Routes:
  GET /              — landing.html (홈 / 소식) — quant.* host serves /q instead
  GET /a/{id}        — account.html (per-account view)
  GET /q             — quant.html (auto-trading cockpit)
  GET /stock         — account.html (legacy alias, will be dropped later)
  /api/universe/*    — symbol master search
  /api/accounts/*    — account CRUD + holdings + balances + snapshots + chat
  /api/market/*      — ohlcv + quotes
  /api/analysis/*    — chart / fundamental / news / judge verdicts + news feed
  /api/quant/*       — auto-trading state, plans, watch, journal

Run:
  python -m src.dashboard_web.server --port 8888 --host 127.0.0.1
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from src.core import security

STATIC = Path(__file__).resolve().parent / "static"
THEME_PATH = STATIC / "theme.json"

# Matches <head>, <head lang="ko">, <HEAD ...> etc.
_HEAD_RE = re.compile(r"<head\b[^>]*>", re.IGNORECASE)

# Interactive docs off: they sit outside /api/* so the PIN gate never sees them,
# and on a public origin they would publish the whole API surface.
app = FastAPI(title="portfolio-dashboard",
              docs_url=None, redoc_url=None, openapi_url=None)

# All /api/* routes live behind a single aggregate router.
try:
    from src.api.router import router as api_router
    app.include_router(api_router)
except Exception as exc:  # pragma: no cover
    logging.warning("api router not mounted: %s", exc)


# /api/* is closed by default — a request needs a valid account session cookie
# (any account). Nothing else guards these routes: /api/analysis/*/run spends
# LLM budget and /api/accounts writes the portfolio. The exceptions below are
# the entry points that have to work before you own a cookie.
_OPEN_API = {
    ("GET", "/api/accounts"),                 # account list — names only
    ("POST", "/api/accounts"),                # account creation — own PIN set in-form
    ("GET", "/api/analysis/news/feed"),       # landing page news, no PII
}


@app.middleware("http")
async def api_session_gate(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and (request.method, path) not in _OPEN_API:
        is_pin_post = request.method == "POST" and path.endswith("/auth")
        if not is_pin_post and not security.has_any_session(request.cookies):
            return JSONResponse({"detail": "PIN required"}, status_code=403)
    return await call_next(request)


_NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


def _hex_to_rgb_csv(hex_str: str) -> str:
    """'#0e1117' → '14, 17, 23'. Used for rgba(var(--xxx-rgb), alpha)."""
    h = hex_str.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return f"{int(h[0:2], 16)}, {int(h[2:4], 16)}, {int(h[4:6], 16)}"
    except Exception:
        return "0, 0, 0"


def _load_theme() -> dict:
    """Full theme dict (colors + tokens) from theme.json, the single source of
    truth for every design literal.

    No built-in fallback palette on purpose: theme.json ships with the code, so
    it can only be missing or corrupt if the deploy itself is broken, and a
    hardcoded second palette would silently serve the wrong (stale) design
    while looking healthy. An unstyled page plus this error line is the honest
    failure."""
    try:
        return json.loads(THEME_PATH.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        logging.error("theme.json unusable, serving unstyled: %s", exc)
        return {}


def _theme_style_tag() -> str:
    """Emit a single <style id="theme-vars"> defining:
      - --xxx + --xxx-rgb  for every color token (rgba(var(--xxx-rgb), a))
      - --xxx              for every non-color token (radius, shadow, …)
    Page CSS references only these tokens — no hardcoded design literals
    allowed in the static files."""
    theme = _load_theme()
    lines = [":root {"]
    for name, hex_val in (theme.get("colors") or {}).items():
        lines.append(f"  --{name}: {hex_val};")
        lines.append(f"  --{name}-rgb: {_hex_to_rgb_csv(hex_val)};")
    for name, val in (theme.get("tokens") or {}).items():
        lines.append(f"  --{name}: {val};")
    lines.append("}")
    return '<style id="theme-vars">\n' + "\n".join(lines) + "\n</style>"


def _serve(name: str) -> HTMLResponse:
    p = STATIC / name
    body = p.read_text() if p.exists() else f"<h1>{name} missing</h1>"
    theme_tag = _theme_style_tag()
    # Inject right after the opening <head ...> tag (any attributes) so theme
    # tokens load before page CSS. Falls back to prepend if no <head> found.
    m = _HEAD_RE.search(body)
    if m:
        body = body[:m.end()] + "\n" + theme_tag + body[m.end():]
    else:
        body = theme_tag + body
    return HTMLResponse(content=body, headers=_NO_CACHE_HEADERS)


# quant.jsyoo.dev and portfolio.jsyoo.dev are the same app behind the same
# nginx block; only the Host header separates them. Session cookies are
# host-only, so the quant origin has its own login — a portfolio session
# cannot reach the order book, and vice versa.
_QUANT_HOST_RE = re.compile(r"^quant\b", re.IGNORECASE)


def _is_quant_host(request: Request) -> bool:
    return bool(_QUANT_HOST_RE.match(request.headers.get("host", "")))


@app.get("/", response_class=HTMLResponse)
def landing(request: Request) -> HTMLResponse:
    if _is_quant_host(request):
        return _serve_quant()
    return _serve("landing.html")


@app.get("/q", response_class=HTMLResponse)
def quant_page() -> HTMLResponse:
    return _serve_quant()


def _serve_quant() -> HTMLResponse:
    """The page is bound to one account, and a fresh browser has no cookie to
    learn which — so the id is injected here rather than opening a public
    endpoint just to name it."""
    from src.quant import store as qstore
    try:
        account_id = qstore.account_id() or ""
    except Exception as exc:  # noqa: BLE001 — an unreachable DB is not a 500 page
        logging.error("quant account lookup failed: %s", exc)
        account_id = ""
    resp = _serve("quant.html")
    body = resp.body.decode()
    tag = f'<script>window.QUANT_ACCOUNT = {json.dumps(account_id)};</script>'
    m = _HEAD_RE.search(body)
    body = (body[:m.end()] + "\n" + tag + body[m.end():]) if m else tag + body
    return HTMLResponse(content=body, headers=_NO_CACHE_HEADERS)


@app.get("/a/{account_id}", response_class=HTMLResponse)
def account_page(account_id: str) -> HTMLResponse:
    # account_id is read by the page's JS from window.location.pathname,
    # so the server just hands over the same shell for every account.
    return _serve("account.html")


# Legacy alias — old /stock bookmarks redirect to the active account view.
@app.get("/stock", response_class=HTMLResponse)
def stock_legacy() -> HTMLResponse:
    return _serve("account.html")


def main() -> None:
    import uvicorn

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8888)
    ap.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
