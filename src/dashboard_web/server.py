"""FastAPI dashboard server for stock-portfolio.

Routes:
  GET /              — news.html (수집: 분야 분위기 · 오늘의 핵심 · 흐름)
  GET /a/{id}        — account.html (per-account view)
  GET /sources       — sources.html (source and job health)
  GET /favicon*.png · /favicon.ico · /apple-touch-icon.png — product icons
  GET /fonts/PretendardVariable.woff2 — the one web font (OFL, static/fonts/)
  /api/universe/*    — symbol master search
  /api/accounts/*    — account CRUD + holdings + balances + snapshots + chat
  /api/market/*      — ohlcv + quotes
  /api/analysis/*    — 종합 분석 + 도메인별 facts + news feed

Run:
  python -m src.dashboard_web.server --port 8888 --host 127.0.0.1
"""
from __future__ import annotations

import argparse
import functools
import json
import logging
import re
import secrets
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from src.portfolio import security

STATIC = Path(__file__).resolve().parent / "static"
THEME_PATH = STATIC / "theme.json"

# Matches <head>, <head lang="ko">, <HEAD ...> etc.
_HEAD_RE = re.compile(r"<head\b[^>]*>", re.IGNORECASE)
_CHARSET_RE = re.compile(r"<meta\s+charset=[^>]*>", re.IGNORECASE)

# Interactive docs off: they sit outside /api/* so the PIN gate never sees them,
# and on a public origin they would publish the whole API surface.
app = FastAPI(title="stock-portfolio",
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
    ("GET", "/api/analysis/news/feed"),       # news page cards, no PII
    ("GET", "/api/analysis/news/themes"),     # the field grid, no PII
    ("GET", "/api/accounts/session"),         # this device's open accounts — [] when none
}
# Prefix-open: a field's panel (no PII) and this device's own lock (clears
# its own cookie only).
_OPEN_PREFIX = (("GET", "/api/analysis/news/themes/"),)
_LOCK_RE = re.compile(r"/api/accounts/[^/]+/lock")


@app.middleware("http")
async def api_session_gate(request: Request, call_next):
    path = request.url.path
    open_ = ((request.method, path) in _OPEN_API
             or any(request.method == m and path.startswith(pre) for m, pre in _OPEN_PREFIX)
             or (request.method == "POST" and _LOCK_RE.fullmatch(path) is not None))
    if path.startswith("/api/") and not open_:
        is_pin_post = request.method == "POST" and path.endswith("/auth")
        # Any account's session opens the shared data. The ledger stays behind
        # require_account, which is closed while an account has no PIN — a
        # token is never the only lock.
        if not is_pin_post and not security.has_any_session(request.cookies):
            return JSONResponse({"detail": "PIN required"}, status_code=403)
    return await call_next(request)


_NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


# ── CSP ────────────────────────────────────────────────────────────────────
# Every <script>/<style> on a page is inline (the page's own pair, the
# fragments injected below and the <!--#include--> pieces), so the policy is a
# per-response nonce, never 'unsafe-inline'. Consequences the static files have
# to live with: a style="…" attribute and setAttribute('style', …) are blocked
# (CSSOM writes — el.style.cssText / el.style.x — are not); news thumbnails and
# source favicons are external https images. Pretendard is served from this
# origin (font-src 'self'); no third-party style or font host is allowed.
_TAG_RE = re.compile(r"<(script|style)\b", re.IGNORECASE)


def _csp(nonce: str) -> str:
    return (f"default-src 'self'; script-src 'nonce-{nonce}'; "
            f"style-src 'nonce-{nonce}'; "
            "font-src 'self'; img-src 'self' data: https:; "
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


def _icon_route(path: str) -> None:
    body, media = _ICONS[path]

    @app.get(path, include_in_schema=False)
    def icon() -> Response:
        return Response(content=body, media_type=media, headers=_ICON_HEADERS)


for _p in _ICONS:
    _icon_route(_p)


# ── font ───────────────────────────────────────────────────────────────────
# Pretendard Variable (OFL-1.1, static/fonts/LICENSE.txt), from the npm
# `pretendard@1.3.9` package. Same treatment as the icons: read once at
# import, a fixed route, immutable cache. Every page preloads it.
FONT_PATH = "/fonts/PretendardVariable.woff2"
_FONT = (STATIC / "fonts" / "PretendardVariable.woff2").read_bytes()
_FONT_LINK = (f'<link rel="preload" href="{FONT_PATH}" as="font" '
              'type="font/woff2" crossorigin>')


@app.get(FONT_PATH, include_in_schema=False)
def font() -> Response:
    return Response(content=_FONT, media_type="font/woff2", headers=_ICON_HEADERS)


# ── static text cache ──────────────────────────────────────────────────────
# The pages and their fragments are read per request so an edit shows without
# a restart; keying the cache on mtime keeps that while making the steady
# state zero disk reads. The nonce in _html stays per response.
@functools.lru_cache(maxsize=64)
def _text_at(path: str, mtime_ns: int) -> str:
    return Path(path).read_text()


def _read(path: Path) -> str:
    """Contents of `path`, re-read only when its mtime changes. Raises
    OSError when the file is missing."""
    return _text_at(str(path), path.stat().st_mtime_ns)


def _mtime(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return -1


def _load_theme() -> dict:
    """theme.json, the single source of truth for every design literal.

    No built-in fallback palette on purpose: theme.json ships with the code, so
    it can only be missing or corrupt if the deploy itself is broken, and a
    hardcoded second palette would silently serve the wrong (stale) design
    while looking healthy. An unstyled page plus this error line is the honest
    failure."""
    try:
        return json.loads(_read(THEME_PATH))
    except (OSError, json.JSONDecodeError) as exc:
        logging.error("theme.json unusable, serving unstyled: %s", exc)
        return {}


# ── design tokens → CSS ──────────────────────────────────────────────────────
# The one naming rule (theme.json "description"): token 'a/b/c' is the CSS
# variable --a-b-c. Semantic tokens point at their primitive through var(), so
# the cascade keeps the alias the design file has.
FONT_STACK = ("'Pretendard Variable', Pretendard, -apple-system, BlinkMacSystemFont, "
              "system-ui, 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif")
_WEIGHTS = {"Thin": 100, "ExtraLight": 200, "Light": 300, "Regular": 400,
            "Medium": 500, "SemiBold": 600, "Bold": 700, "ExtraBold": 800, "Black": 900}


def css_var(name: str) -> str:
    """'bg/default' → '--bg-default'."""
    return "--" + name.replace("/", "-")


def _token_value(tok: dict) -> str | None:
    """A design-file token's literal CSS value, or None when it has no CSS
    form (primitive/icon-set is metadata)."""
    name, typ, val = tok["name"], tok.get("type"), tok.get("value")
    if tok.get("layer") == "code":
        return str(val)
    if typ == "color":
        return str(val)
    if typ == "number":
        return f"{val}px"
    if name.startswith("primitive/font/"):
        return FONT_STACK
    if name.startswith("primitive/weight/"):
        return str(_WEIGHTS[val])
    return None


def theme_values(theme: dict | None = None) -> dict:
    """Every token name → its resolved literal (aliases followed), for the
    JS side (canvas cannot render var()) and for the tests."""
    theme = _load_theme() if theme is None else theme
    toks = list(theme.get("tokens") or []) + list(theme.get("code_tokens") or [])
    by_name = {t["name"]: t for t in toks}
    out = {}
    for t in toks:
        cur, seen = t, set()
        while "alias" in cur and cur["name"] not in seen:
            seen.add(cur["name"])
            cur = by_name[cur["alias"]]
        v = _token_value(cur)
        if v is not None:
            out[t["name"]] = v
    return out


def theme_css(theme: dict) -> list[str]:
    toks = list(theme.get("tokens") or []) + list(theme.get("code_tokens") or [])
    values = theme_values(theme)
    lines = []
    for t in toks:
        if t["name"] not in values:
            continue
        val = f"var({css_var(t['alias'])})" if "alias" in t else values[t["name"]]
        lines.append(f"  {css_var(t['name'])}: {val};")
    return lines


def _theme_style_tag() -> str:
    """One <style id="theme-vars"> with every token as a CSS variable, plus the
    same values as window.__THEME_TOKENS for the shared JS. Page CSS
    references only these variables — no design literals in the static files."""
    return _theme_style_tag_at(_mtime(THEME_PATH))


@functools.lru_cache(maxsize=4)
def _theme_style_tag_at(mtime_ns: int) -> str:
    theme = _load_theme()
    lines = [":root {"] + theme_css(theme) + ["}"] if theme else [":root {", "}"]
    values = theme_values(theme) if theme else {}
    js = {
        "values": values,
        "themes": theme.get("themes") or {},
    }
    tokens = json.dumps(js, ensure_ascii=False)
    return ('<style id="theme-vars">\n' + "\n".join(lines) + "\n</style>\n"
            f'<script>window.__THEME_TOKENS = {tokens};</script>')


def _fragment_tag(name: str, open_tag: str, close_tag: str) -> str:
    """One shared fragment (shared.css / shared.js) as an inline tag. No
    fallback copy, and no page without it either: every page's first line is
    `const { el, … } = S`, so a missing fragment is a blank screen — a 500 is
    what the deploy health check can see."""
    try:
        return open_tag + "\n" + _read(STATIC / name) + "\n" + close_tag
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


# ── page fragments ────────────────────────────────────────────────────────
# A page can be several files: `<!--#include account/stock.js-->` in the HTML
# is replaced by that file under static/, inline — .js as a <script>, .css as
# a <style>, .html as is — so the nonce CSP stamps it like any other tag and
# no /static mount exists. Same mtime cache as the pages; a missing fragment
# is a 500, never a page with a hole in it.
_INCLUDE_RE = re.compile(r"<!--#include\s+([^\s>]+?)\s*-->")
_INCLUDE_TAGS = {".js": ("<script data-include=\"{}\">", "</script>"),
                 ".css": ("<style data-include=\"{}\">", "</style>"),
                 ".html": ("", "")}


def _include(rel: str) -> str:
    path = (STATIC / rel).resolve()
    suffix = path.suffix.lower()
    if (suffix not in _INCLUDE_TAGS or not path.is_relative_to(STATIC.resolve())
            or not re.fullmatch(r"[\w./-]+", rel)):
        logging.error("include refused: %s", rel)
        raise HTTPException(status_code=500, detail=f"include {rel} refused")
    try:
        text = _read(path)
    except OSError as exc:
        logging.error("include %s unusable: %s", rel, exc)
        raise HTTPException(status_code=500, detail=f"{rel} missing") from exc
    open_tag, close_tag = _INCLUDE_TAGS[suffix]
    if close_tag and close_tag[:-1].lower() in text.lower():
        # '</script' inside a script ends the tag early and spills the rest as markup
        raise HTTPException(status_code=500, detail=f"{rel} contains {close_tag[:-1]}")
    if not open_tag:
        return text
    return open_tag.format(rel) + "\n" + text + "\n" + close_tag


def expand_includes(body: str) -> str:
    return _INCLUDE_RE.sub(lambda m: _include(m.group(1)), body)


def _serve(name: str) -> HTMLResponse:
    try:
        body = _read(STATIC / name)
    except OSError:
        body = f"<h1>{name} missing</h1>"
    body = expand_includes(body)
    # <head> order: icons + font preload → theme tokens → shared.css →
    # shared.js → the page's own <style>/<script>. CSS arriving first lets
    # page rules override it (the cascade); JS arriving first is why the
    # fragment exports one namespace (window.S) instead of globals a page
    # could shadow.
    tags = [_ICON_LINKS, _FONT_LINK, _theme_style_tag(), _shared_css_tag(), _shared_js_tag()]
    return _html(_inject_head(body, "\n".join(tags)))


_PORTFOLIO_HOST_RE = re.compile(r"^portfolio\b", re.IGNORECASE)


def _is_portfolio_host(request: Request) -> bool:
    return bool(_PORTFOLIO_HOST_RE.match(request.headers.get("host", "")))


@app.get("/", response_class=HTMLResponse)
def news_page() -> HTMLResponse:
    return _serve("news.html")


@app.get("/sources", response_class=HTMLResponse)
def sources_page() -> HTMLResponse:
    # Data comes from /api/health/sources, behind the session gate.
    return _serve("sources.html")


@app.get("/a/{account_id}", response_class=HTMLResponse)
def account_page(account_id: str) -> HTMLResponse:
    # account_id is read by the page's JS from window.location.pathname,
    # so the server just hands over the same shell for every account.
    return _serve("account.html")


# Unknown page paths answer with a page, not FastAPI's JSON string — a person
# typed that URL. It carries the product's wordmark on the public host and is
# styled from the same theme tokens. /api/* keeps the JSON body.
_NOT_FOUND_HTML = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>404</title>
<style>
body { margin: 0; min-height: 100vh; display: grid; place-items: center;
       background: var(--bg-subtle); color: var(--text-primary);
       font-family: var(--type-body-family); }
main { text-align: center; }
.mark { font-size: var(--type-body-size); letter-spacing: .12em; color: var(--text-muted);
        margin-bottom: var(--space-gap-lg); }
h1 { font-size: var(--type-number-lg-size); margin: 0 0 var(--space-gap-sm); font-weight: 700; }
p { color: var(--text-muted); margin: 0; font-size: var(--type-body-size); }
</style>
</head>
<body><main>
<div class="mark">__MARK__</div>
<h1>404</h1>
<p>없는 페이지예요</p>
</main></body>
</html>
"""


@app.exception_handler(404)
def not_found(request: Request, exc: Exception):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": getattr(exc, "detail", "Not Found")}, status_code=404)
    mark = "◆ PORTFOLIO" if _is_portfolio_host(request) else ""
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
