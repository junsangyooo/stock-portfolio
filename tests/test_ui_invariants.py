"""Screen invariants, enforced from source the way test_quant_api.py enforces
require_quant: the two products never reference each other, and each public
Host serves only its own pages (ops notes §2.2 — same app, same nginx block;
only the Host header and host-only cookies separate them).
"""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.dashboard_web import server
from src.quant import db as qdb

STATIC = Path(server.__file__).resolve().parent / "static"
PORTFOLIO_HOST = "portfolio.jsyoo.dev"
QUANT_HOST = "quant.jsyoo.dev"


@pytest.fixture
def client(tmp_path, monkeypatch):
    # /q names the quant account from quant.db — point the book at a tmp file.
    monkeypatch.setattr(qdb, "QUANT_DB_PATH", tmp_path / "quant.db")
    monkeypatch.setattr(qdb, "_initialized", False)
    qdb.forget_account_id()
    return TestClient(server.app)


def _hits(name: str, patterns: list[str]) -> list[str]:
    lines = (STATIC / name).read_text().splitlines()
    return [f"{name}:{i}: {line.strip()}"
            for i, line in enumerate(lines, 1)
            if any(re.search(p, line, re.IGNORECASE) for p in patterns)]


def test_no_crosslinks_between_products():
    """Case-insensitive on purpose: with case respected a wordmark like
    '◆ QUANT-LAB' on the portfolio screen passes untouched."""
    to_quant = [r"/q\b", r"quant", r"pdash_q"]        # 'quant' covers /api/quant
    to_portfolio = [r"/a/", r"/api/accounts", r"pdash_a"]
    bad = (_hits("landing.html", to_quant)
           + _hits("account.html", to_quant)
           + _hits("quant.html", to_portfolio))
    assert not bad, "cross-product references:\n" + "\n".join(bad)


def test_host_gate_on_page_routes(client):
    """404, never a redirect — a redirect is itself a path between the
    products. Deny-list: only the two public hosts are refused each other's
    page, so local and IP access (laptop, Oracle shell) keeps working."""
    def get(path, host):
        return client.get(path, headers={"host": host}, follow_redirects=False)

    assert get("/a/x", QUANT_HOST).status_code == 404
    assert get("/q", PORTFOLIO_HOST).status_code == 404
    assert get("/q", QUANT_HOST).status_code == 200
    assert get("/a/x", PORTFOLIO_HOST).status_code == 200
    for host in ("127.0.0.1:8888", "localhost:8888"):
        assert get("/q", host).status_code == 200, host
        assert get("/a/x", host).status_code == 200, host


def test_quant_page_not_served_on_portfolio_host(client):
    """The account id is injected by _serve_quant() only; its absence on the
    portfolio host is the check (a string search on the id would match the
    hardcoded fallback in account.html)."""
    tag = "window.QUANT_ACCOUNT"
    for path in ("/", "/q"):
        r = client.get(path, headers={"host": PORTFOLIO_HOST})
        assert tag not in r.text, path
    assert tag in client.get("/q", headers={"host": QUANT_HOST}).text
