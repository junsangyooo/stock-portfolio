"""FastAPI dashboard server for portfolio-dashboard.

Routes:
  GET /              — landing.html (홈 / 소식) — quant.* host serves /q instead
  GET /a/{id}        — account.html (per-account view)
  GET /q             — quant.html (auto-trading cockpit)
  GET /favicon*.png · /favicon.ico · /apple-touch-icon.png — product icons
  /api/universe/*    — symbol master search
  /api/accounts/*    — account CRUD + holdings + balances + snapshots + chat
  /api/market/*      — ohlcv + quotes
  /api/analysis/*    — 종합 분석 + 도메인별 facts + news feed
  /api/quant/*       — auto-trading state, plans, watch, journal

Run:
  python -m src.dashboard_web.server --port 8888 --host 127.0.0.1
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import secrets
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from src.core import security

STATIC = Path(__file__).resolve().parent / "static"
THEME_PATH = STATIC / "theme.json"

# Matches <head>, <head lang="ko">, <HEAD ...> etc.
_HEAD_RE = re.compile(r"<head\b[^>]*>", re.IGNORECASE)
_CHARSET_RE = re.compile(r"<meta\s+charset=[^>]*>", re.IGNORECASE)

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


# /api/quant/* is exempt from the aggregate gate below and gated by its own
# router instead: every route there carries Depends(require_quant), which
# checks the quant book's OWN cookie and PIN. The aggregate gate only asks
# "does this request carry some portfolio account session", which is both too
# weak for the order book and — since the quant cookie is not pdash_a_* —
# unable to recognise a legitimate quant login at all.
_SELF_GATED_PREFIX = "/api/quant/"


@app.middleware("http")
async def api_session_gate(request: Request, call_next):
    path = request.url.path
    if (path.startswith("/api/")
            and not path.startswith(_SELF_GATED_PREFIX)
            and (request.method, path) not in _OPEN_API):
        is_pin_post = request.method == "POST" and path.endswith("/auth")
        # A quant login opens the shared data too: its token names its own
        # account and the HMAC covers it, so no quant.db lookup is needed.
        # The ledgers stay behind require_account / require_quant, which are
        # closed while a book has no PIN — a token is never the only lock.
        has_session = (security.has_any_session(request.cookies)
                       or security.token_is_valid(request.cookies.get(security.QUANT_COOKIE)))
        if not is_pin_post and not has_session:
            return JSONResponse({"detail": "PIN required"}, status_code=403)
    return await call_next(request)


_NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


# ── CSP ────────────────────────────────────────────────────────────────────
# Every <script>/<style> on a page is inline (the page's own pair plus the
# fragments injected below), so the policy is a per-response nonce, never
# 'unsafe-inline'. Consequences the static files have to live with: a
# style="…" attribute and setAttribute('style', …) are blocked (CSSOM writes —
# el.style.cssText / el.style.x — are not); Google Fonts is reached through
# @import inside the nonced <style>, hence the fonts host in style-src; news
# thumbnails and source favicons are external https images.
_TAG_RE = re.compile(r"<(script|style)\b", re.IGNORECASE)


def _csp(nonce: str) -> str:
    return (f"default-src 'self'; script-src 'nonce-{nonce}'; "
            f"style-src 'nonce-{nonce}' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com; img-src 'self' data: https:; "
            "connect-src 'self'; frame-ancestors 'self'; base-uri 'self'; "
            "form-action 'self'")


def _html(body: str, status_code: int = 200) -> HTMLResponse:
    """Every HTML response leaves through here: one fresh nonce, stamped on
    every <script>/<style> tag in the finished document (the page's own and
    the injected ones alike) and named in the CSP header."""
    nonce = secrets.token_urlsafe(16)
    body = _TAG_RE.sub(lambda m: f'<{m.group(1)} nonce="{nonce}"', body)
    return HTMLResponse(content=body, status_code=status_code,
                        headers={**_NO_CACHE_HEADERS, "Content-Security-Policy": _csp(nonce)})


# ── icons ──────────────────────────────────────────────────────────────────
# Read once at import, served from memory on fixed routes — not a /static
# mount (§11 화면). Regenerate with deploy/make_icons.py.
_ICON_HEADERS = {"Cache-Control": "public, max-age=31536000, immutable"}
_ICONS = {path: ((STATIC / "icons" / name).read_bytes(), media)
          for path, name, media in (
              ("/favicon.ico", "favicon.ico", "image/x-icon"),
              ("/favicon-32.png", "favicon-32.png", "image/png"),
              ("/favicon-64.png", "favicon-64.png", "image/png"),
              ("/apple-touch-icon.png", "apple-touch-icon-180.png", "image/png"))}
_ICON_LINKS = ('<link rel="icon" type="image/png" sizes="32x32" href="/favicon-32.png">\n'
               '<link rel="icon" type="image/png" sizes="64x64" href="/favicon-64.png">\n'
               '<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">')
# The earlier inline SVG favicon line in the pages; dropped at serve time.
_SVG_FAVICON_RE = re.compile(r'[ \t]*<link rel="icon" href="data:image/svg\+xml[^>]*>\n?')


def _icon_route(path: str) -> None:
    body, media = _ICONS[path]

    @app.get(path, include_in_schema=False)
    def icon() -> Response:
        return Response(content=body, media_type=media, headers=_ICON_HEADERS)


for _p in _ICONS:
    _icon_route(_p)


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
    # The same tokens as JSON for the shared JS: canvas cannot render
    # 'var(--x)', and the per-news-theme colours/icons have no CSS form.
    tokens = json.dumps({k: theme.get(k) or {} for k in ("colors", "tokens", "themes")},
                        ensure_ascii=False)
    return ('<style id="theme-vars">\n' + "\n".join(lines) + "\n</style>\n"
            f'<script>window.__THEME_TOKENS = {tokens};</script>')


def _fragment_tag(name: str, open_tag: str, close_tag: str) -> str:
    """One shared fragment (shared.css / shared.js) as an inline tag. No
    fallback copy, and no page without it either: every page's first line is
    `const { el, … } = S`, so a missing fragment is a blank screen — a 500 is
    what the deploy health check can see."""
    try:
        return open_tag + "\n" + (STATIC / name).read_text() + "\n" + close_tag
    except OSError as exc:
        logging.error("%s unusable: %s", name, exc)
        raise HTTPException(status_code=500, detail=f"{name} missing") from exc


def _shared_css_tag() -> str:
    return _fragment_tag("shared.css", '<style id="shared-css">', "</style>")


def _shared_js_tag() -> str:
    return _fragment_tag("shared.js", '<script id="shared-js">', "</script>")


def _inject_head(body: str, tag: str) -> str:
    """Insert `tag` right after <meta charset> (so the ~45KB of fragments
    never push the charset declaration out of the browser's sniffing window),
    else after the opening <head ...>, else prepend."""
    head = _HEAD_RE.search(body)
    if not head:
        return tag + body
    at = head.end()
    charset = _CHARSET_RE.search(body, at)
    if charset:
        at = charset.end()
    return body[:at] + "\n" + tag + body[at:]


def _serve(name: str, extra_tag: str = "") -> HTMLResponse:
    p = STATIC / name
    body = p.read_text() if p.exists() else f"<h1>{name} missing</h1>"
    body = _SVG_FAVICON_RE.sub("", body)
    # <head> order: icons → theme tokens → shared.css → shared.js → extra →
    # the page's own <style>/<script>. CSS arriving first lets page rules
    # override it (the cascade); JS arriving first is why the fragment exports
    # one namespace (window.S) instead of globals a page could shadow.
    tags = [_ICON_LINKS, _theme_style_tag(), _shared_css_tag(), _shared_js_tag(), extra_tag]
    return _html(_inject_head(body, "\n".join(t for t in tags if t)))


# quant.jsyoo.dev and portfolio.jsyoo.dev are the same app behind the same
# nginx block; only the Host header separates them. Session cookies are
# host-only, so the quant origin has its own login — a portfolio session
# cannot reach the order book, and vice versa.
_QUANT_HOST_RE = re.compile(r"^quant\b", re.IGNORECASE)
_PORTFOLIO_HOST_RE = re.compile(r"^portfolio\b", re.IGNORECASE)


def _is_quant_host(request: Request) -> bool:
    return bool(_QUANT_HOST_RE.match(request.headers.get("host", "")))


def _is_portfolio_host(request: Request) -> bool:
    return bool(_PORTFOLIO_HOST_RE.match(request.headers.get("host", "")))


# Page routes refuse the *other* product's public host with a 404 — never a
# redirect, which would itself be a path between the two products. It is a
# deny-list, not an allow-list: a host that is neither public origin (laptop,
# Oracle shell, the deploy health check) reaches every page.
@app.get("/", response_class=HTMLResponse)
def landing(request: Request) -> HTMLResponse:
    if _is_quant_host(request):
        return _serve_quant()
    return _serve("landing.html")


@app.get("/q", response_class=HTMLResponse)
def quant_page(request: Request) -> HTMLResponse:
    if _is_portfolio_host(request):
        raise HTTPException(status_code=404)
    return _serve_quant()


def _serve_quant() -> HTMLResponse:
    """The page is bound to one account, and a fresh browser has no cookie to
    learn which — so the id is injected here rather than opening a public
    endpoint just to name it."""
    from src.quant import store as qstore
    try:
        account_id = qstore.primary_account_id() or ""
    except Exception as exc:  # noqa: BLE001 — an unreachable DB is not a 500 page
        logging.error("quant account lookup failed: %s", exc)
        account_id = ""
    return _serve("quant.html",
                  f'<script>window.QUANT_ACCOUNT = {json.dumps(account_id)};</script>')


@app.get("/a/{account_id}", response_class=HTMLResponse)
def account_page(request: Request, account_id: str) -> HTMLResponse:
    if _is_quant_host(request):
        raise HTTPException(status_code=404)
    # account_id is read by the page's JS from window.location.pathname,
    # so the server just hands over the same shell for every account.
    return _serve("account.html")


# The Host gate and unknown page paths answer with a page, not FastAPI's
# JSON string — a person typed that URL. It names only the requesting host's
# own product (no path to the other one, per the cross-link invariant) and
# is styled from the same theme tokens. /api/* keeps the JSON body.
_NOT_FOUND_HTML = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>404</title>
<style>
body { margin: 0; min-height: 100vh; display: grid; place-items: center;
       background: var(--bg); color: var(--fg); font-family: ui-monospace, monospace; }
main { text-align: center; }
.mark { font-size: 14px; letter-spacing: .12em; color: var(--dim); margin-bottom: 18px; }
h1 { font-size: 48px; margin: 0 0 8px; font-weight: 500; }
p { color: var(--muted); margin: 0; font-size: 13px; }
</style>
</head>
<body><main>
<div class="mark">__MARK__</div>
<h1>404</h1>
<p>없는 페이지입니다.</p>
</main></body>
</html>
"""


@app.exception_handler(404)
def not_found(request: Request, exc: Exception):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": getattr(exc, "detail", "Not Found")}, status_code=404)
    mark = ("◆ QUANT" if _is_quant_host(request)
            else "◆ PORTFOLIO" if _is_portfolio_host(request) else "")
    body = _NOT_FOUND_HTML.replace("__MARK__", mark)
    return _html(_inject_head(body, _theme_style_tag()), status_code=404)


def main() -> None:
    import uvicorn

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8888)
    ap.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
