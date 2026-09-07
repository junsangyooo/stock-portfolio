"""Throwaway probe for the plan_runs routes."""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from src.core import security
from src.dashboard_web.server import app
from src.portfolio.store import account_store, db, plan_store
from src.api import analysis_jobs


@pytest.fixture
def book(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "portfolio.db")
    monkeypatch.setattr(db, "_initialized", False)
    account_store.create("isa", "ISA", "ISA")
    account_store.set_pin_hash("isa", security.hash_pin("isa", "111111"))
    account_store.create("gen", "GEN", "GENERAL")
    account_store.set_pin_hash("gen", security.hash_pin("gen", "222222"))
    yield tmp_path


def ck(acct):
    return {security.COOKIE_PREFIX + acct: security.make_session_token(acct)}


def test_route_order_latest(book):
    c = TestClient(app)
    r = c.get("/api/accounts/isa/plan_runs/latest", cookies=ck("isa"))
    print("LATEST", r.status_code, r.json())
    assert r.status_code == 200
    r = c.get("/api/accounts/isa/plan_runs/2026-09-07", cookies=ck("isa"))
    print("DATE", r.status_code, r.json())
    r = c.get("/api/accounts/isa/plan_runs", cookies=ck("isa"))
    print("LIST", r.status_code, r.json())
    r = c.get("/api/accounts/isa/plan_runs/status", cookies=ck("isa"))
    print("STATUS", r.status_code, r.json())


def test_routes_all_gated(book):
    c = TestClient(app)
    for m, p in [("GET", "/api/accounts/isa/plan_runs/latest"),
                 ("POST", "/api/accounts/isa/plan_runs/run"),
                 ("GET", "/api/accounts/isa/plan_runs/status"),
                 ("GET", "/api/accounts/isa/plan_runs"),
                 ("GET", "/api/accounts/isa/plan_runs/2026-09-07")]:
        r = c.request(m, p)   # no cookie at all
        print("NOCOOKIE", m, p, r.status_code)
        r2 = c.request(m, p, cookies=ck("gen"))   # OTHER account's cookie
        print("OTHERCOOKIE", m, p, r2.status_code, r2.text[:120])


def test_cross_account_preview(book, monkeypatch):
    """Can gen's cookie start isa's preview?"""
    started = []
    monkeypatch.setattr(analysis_jobs, "start",
                        lambda k, fn: started.append(k) or {"state": "started"})
    c = TestClient(app)
    r = c.post("/api/accounts/isa/plan_runs/run", cookies=ck("gen"))
    print("CROSS RUN", r.status_code, r.text[:200], "started=", started)
    r = c.post("/api/accounts/isa/plan_runs/run", cookies=ck("isa"))
    print("OWN RUN", r.status_code, r.text[:200], "started=", started)
