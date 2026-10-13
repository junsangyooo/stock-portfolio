"""Every HTML response carries a per-response nonce CSP (ui-open-decisions B4)
and a page 404 is an HTML page, not a JSON string (B8)."""
import re

import pytest
from fastapi.testclient import TestClient

from src.dashboard_web import server

PORTFOLIO_HOST = "portfolio.jsyoo.dev"
PAGES = (("/", PORTFOLIO_HOST), ("/a/x", PORTFOLIO_HOST))
_TAG = re.compile(r"<(script|style)\b[^>]*>", re.IGNORECASE)
_NONCE = re.compile(r"script-src 'nonce-([A-Za-z0-9_-]{16,})'")


@pytest.fixture
def client():
    return TestClient(server.app)


def _nonce(r):
    m = _NONCE.search(r.headers.get("content-security-policy", ""))
    assert m, r.headers.get("content-security-policy")
    return m.group(1)


def test_csp_header_and_nonce_on_every_tag(client):
    for path, host in PAGES:
        r = client.get(path, headers={"host": host})
        assert r.status_code == 200, path
        nonce = _nonce(r)
        csp = r.headers["content-security-policy"]
        assert f"style-src 'nonce-{nonce}' https://fonts.googleapis.com" in csp
        assert "font-src https://fonts.gstatic.com" in csp
        assert "unsafe-inline" not in csp and "unsafe-hashes" not in csp
        tags = _TAG.findall(r.text)
        assert len(tags) >= 6, (path, len(tags))          # page 2 + injected ≥4
        bare = [t.group(0) for t in _TAG.finditer(r.text) if f'nonce="{nonce}"' not in t.group(0)]
        assert not bare, (path, bare)


def test_nonce_is_fresh_per_response(client):
    a = client.get("/", headers={"host": PORTFOLIO_HOST})
    b = client.get("/", headers={"host": PORTFOLIO_HOST})
    assert _nonce(a) != _nonce(b)
    assert f'nonce="{_nonce(a)}"' not in b.text


def test_api_and_icons_carry_no_csp(client):
    assert "content-security-policy" not in client.get("/api/accounts").headers
    assert "content-security-policy" not in client.get("/favicon-32.png").headers


def test_svg_favicon_replaced_by_png_links(client):
    for path, host in PAGES:
        body = client.get(path, headers={"host": host}).text
        assert "data:image/svg+xml" not in body, path
        assert 'sizes="32x32" href="/favicon-32.png"' in body
        assert 'sizes="64x64" href="/favicon-64.png"' in body
        assert '<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png">' in body
        assert body.index("<meta charset") < body.index("/favicon-32.png") < body.index('id="theme-vars"')


def test_page_404_is_html_with_the_wordmark_only(client):
    r = client.get("/q", headers={"host": PORTFOLIO_HOST}, follow_redirects=False)
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("text/html")
    assert "◆ PORTFOLIO" in r.text
    assert not re.search(r"quant|/a/|pdash_q", r.text, re.IGNORECASE)
    nonce = _nonce(r)
    assert all(f'nonce="{nonce}"' in t.group(0) for t in _TAG.finditer(r.text))
    assert 'id="theme-vars"' in r.text


def test_unknown_page_path_404_html_and_api_404_json(client):
    r = client.get("/nope", headers={"host": "127.0.0.1:8888"})
    assert r.status_code == 404 and r.headers["content-type"].startswith("text/html")
    assert "404" in r.text and "◆" not in r.text
    # An unknown /api path behind a valid session reaches the router: the
    # answer must stay JSON, never the HTML page.
    from src.portfolio import security
    cookies = {security.COOKIE_PREFIX + "x": security.make_session_token("x")}
    r = client.get("/api/nope", headers={"host": PORTFOLIO_HOST}, cookies=cookies)
    assert r.status_code == 404 and r.json() == {"detail": "Not Found"}
    assert "content-security-policy" not in r.headers
