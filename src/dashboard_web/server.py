"""FastAPI dashboard server for portfolio-dashboard.

Routes:
  GET /              — landing.html (홈 / 소식)
  GET /a/{id}        — account.html (per-account view)
  GET /stock         — account.html (legacy alias, will be dropped later)
  /api/universe/*    — symbol master search
  /api/accounts/*    — account CRUD + holdings + balances + snapshots
  /api/market/*      — ohlcv + quote
  /api/analysis/*    — chart/fundamental/news (Phase 2 stub)
  /static/*          — static assets

Run:
  python -m src.dashboard_web.server --port 8888 --host 127.0.0.1
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

STATIC = Path(__file__).resolve().parent / "static"
THEME_PATH = STATIC / "theme.json"

# Built-in fallback palette. theme.json is the single source of truth, but if
# it's missing or mid-write/corrupt we still emit a usable :root so the page
# never renders unstyled. Kept here (server) — NOT duplicated into the static
# HTML, so the pages stay free of hardcoded color literals.
_FALLBACK_COLORS = {
    "bg": "#0e1117", "panel": "#161b22", "elevated": "#1c222b",
    "grid": "#21262d", "line": "#30363d", "line-strong": "#484f58",
    "fg": "#e6edf3", "dim": "#8a96a8", "muted": "#6e7681",
    "cyan": "#4dd0e1", "pink": "#ff8db1", "green": "#56d364",
    "yellow": "#e3b341", "orange": "#ff9852", "red": "#f85149",
    "purple": "#b392f0",
}

# Matches <head>, <head lang="ko">, <HEAD ...> etc.
_HEAD_RE = re.compile(r"<head\b[^>]*>", re.IGNORECASE)

app = FastAPI(title="portfolio-dashboard")

# All /api/* routes live behind a single aggregate router.
try:
    from src.api.router import router as api_router
    app.include_router(api_router)
except Exception as exc:  # pragma: no cover
    logging.warning("api router not mounted: %s", exc)


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
    """Full theme dict (colors + tokens). On failure returns the fallback
    palette under a 'colors' key so the page is never left unstyled."""
    if not THEME_PATH.exists():
        return {"colors": _FALLBACK_COLORS, "tokens": {}}
    try:
        t = json.loads(THEME_PATH.read_text())
    except Exception as exc:
        logging.warning("theme.json parse failed, using fallback palette: %s", exc)
        return {"colors": _FALLBACK_COLORS, "tokens": {}}
    if not (t.get("colors") or {}):
        t["colors"] = _FALLBACK_COLORS
    return t


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


@app.get("/", response_class=HTMLResponse)
def landing() -> HTMLResponse:
    return _serve("landing.html")


@app.get("/a/{account_id}", response_class=HTMLResponse)
def account_page(account_id: str) -> HTMLResponse:
    # account_id is read by the page's JS from window.location.pathname,
    # so the server just hands over the same shell for every account.
    return _serve("account.html")


# Legacy alias — old /stock bookmarks redirect to the active account view.
@app.get("/stock", response_class=HTMLResponse)
def stock_legacy() -> HTMLResponse:
    return _serve("account.html")


if STATIC.exists():
    app.mount("/static", StaticFiles(directory=STATIC), name="static")


def main() -> None:
    import uvicorn

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8888)
    ap.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
