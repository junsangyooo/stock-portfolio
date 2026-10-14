"""Pretendard is served like the icons (fixed route, memory, immutable) and
preloaded by every page; `<!--#include x-->` puts a static/ file inline under
the page's nonce, and a missing one is a 500 — redesign stage 1."""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.dashboard_web import server

STATIC = Path(server.__file__).resolve().parent / "static"
HOST = {"host": "portfolio.jsyoo.dev"}


@pytest.fixture
def client():
    return TestClient(server.app)


def test_font_route(client):
    r = client.get("/fonts/PretendardVariable.woff2")
    assert r.status_code == 200
    assert r.headers["content-type"] == "font/woff2"
    assert r.content[:4] == b"wOF2"
    cc = r.headers["cache-control"]
    assert "immutable" in cc and "no-cache" not in cc
    assert (STATIC / "fonts" / "LICENSE.txt").read_text().count("SIL Open Font License") >= 1


def test_every_page_preloads_the_font(client):
    for path in ("/", "/a/x", "/sources"):
        body = client.get(path, headers=HOST).text
        assert ('<link rel="preload" href="/fonts/PretendardVariable.woff2" as="font" '
                'type="font/woff2" crossorigin>') in body, path
        assert body.index("/fonts/PretendardVariable.woff2") < body.index('id="shared-css"')
    css = (STATIC / "shared.css").read_text()
    assert "@font-face" in css and "url('/fonts/PretendardVariable.woff2')" in css
    assert "font-variant-numeric: tabular-nums" in css


def _page(tmp_path, monkeypatch, html, files):
    for name in ("theme.json", "shared.css", "shared.js"):
        (tmp_path / name).write_text((STATIC / name).read_text())
    (tmp_path / "news.html").write_text(html)          # what `/` serves
    for rel, text in files.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    monkeypatch.setattr(server, "STATIC", tmp_path)


SHELL = "<html><head><meta charset=utf-8>{}</head><body>{}</body></html>"


def test_include_inlines_by_extension_under_the_nonce(client, monkeypatch, tmp_path):
    _page(tmp_path, monkeypatch,
          SHELL.format("<!--#include account/a.css-->", "<!--#include account/b.html--><!--#include account/c.js-->"),
          {"account/a.css": ".x { color: var(--text-primary); }",
           "account/b.html": "<section id=part>part</section>",
           "account/c.js": "window.C = 1;"})
    r = client.get("/", headers=HOST)
    assert r.status_code == 200
    nonce = re.search(r"script-src 'nonce-([^']+)'", r.headers["content-security-policy"]).group(1)
    assert f'<style nonce="{nonce}" data-include="account/a.css">' in r.text
    assert f'<script nonce="{nonce}" data-include="account/c.js">' in r.text
    assert "<section id=part>part</section>" in r.text
    assert "#include" not in r.text


def test_include_sees_edits_without_restart(client, monkeypatch, tmp_path):
    _page(tmp_path, monkeypatch, SHELL.format("", "<!--#include p.js-->"), {"p.js": "window.V = 1;"})
    assert "window.V = 1;" in client.get("/", headers=HOST).text
    import os
    (tmp_path / "p.js").write_text("window.V = 22;")
    st = (tmp_path / "p.js").stat()
    os.utime(tmp_path / "p.js", ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
    assert "window.V = 22;" in client.get("/", headers=HOST).text


@pytest.mark.parametrize("ref, files", [
    ("missing.js", {}),                                   # not there
    ("../server.py", {}),                                 # outside static/
    ("x.txt", {"x.txt": "no"}),                           # not a page piece
    ("bad.js", {"bad.js": "var s = '</script>';"}),       # would end the tag early
])
def test_include_failures_are_500(client, monkeypatch, tmp_path, ref, files):
    _page(tmp_path, monkeypatch, SHELL.format("", f"<!--#include {ref}-->"), files)
    r = client.get("/", headers=HOST)
    assert r.status_code == 500


def test_no_page_includes_the_old_card_css(client):
    # legacy.css went with the old landing page; nothing may point at it
    assert not (STATIC / "legacy.css").exists()
    for name in ("news.html", "account.html", "sources.html"):
        assert "legacy.css" not in (STATIC / name).read_text(), name
    body = client.get("/", headers=HOST).text
    assert 'data-include="legacy.css"' not in body and 'data-include="news/news.js"' in body
    assert "<!--#include" not in (STATIC / "sources.html").read_text()
