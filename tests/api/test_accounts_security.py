"""PIN attempts, the PIN again before a takeover, the device lock, the
account menu's session list and the add dialog's guard (인계 §4)."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import accounts
from src.portfolio import security
from src.portfolio.store import account_store, db as pdb, holding_store


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(pdb, "DB_PATH", str(tmp_path / "sec.db"))
    monkeypatch.setattr(pdb, "_initialized", False)
    monkeypatch.setattr(security, "_fails", {})
    monkeypatch.setattr(accounts.uni_search, "get", lambda sid: {"currency": "KRW"})
    monkeypatch.setattr(accounts.gap_filler, "fill_one", lambda sid: 0)
    monkeypatch.setattr(accounts, "ensure_theme_mapping", lambda sid: None)
    for aid in ("alice", "bob"):
        account_store.create(aid, aid, "GENERAL")
        account_store.set_pin_hash(aid, security.hash_pin(aid, "123456"))
    app = FastAPI()
    app.include_router(accounts.router)
    return TestClient(app)


def _cookie(aid="alice"):
    return {security.COOKIE_PREFIX + aid: security.make_session_token(aid)}


def test_five_wrong_pins_lock_the_account_for_a_while(client):
    for _ in range(security.MAX_TRIES):
        assert client.post("/api/accounts/alice/auth", json={"pin": "000000"}).status_code == 403
    r = client.post("/api/accounts/alice/auth", json={"pin": "123456"})
    assert r.status_code == 429 and "분 뒤" in r.json()["detail"]
    assert client.post("/api/accounts/bob/auth", json={"pin": "123456"}).status_code == 200


def test_the_lock_lifts_after_its_window():
    security._fails.clear()
    for i in range(security.MAX_TRIES):
        security.check_pin("x", "1", "nope", now=1000 + i)
    assert security.pin_locked_for("x", now=1010) > 0
    assert security.pin_locked_for("x", now=1004 + security.LOCK_S + 1) == 0


def test_a_pin_change_needs_the_current_pin(client):
    r = client.post("/api/accounts/alice/pin", json={"current": "999999", "pin": "654321"},
                    cookies=_cookie())
    assert r.status_code == 403
    r = client.post("/api/accounts/alice/pin", json={"current": "123456", "pin": "654321"},
                    cookies=_cookie())
    assert r.status_code == 200
    assert security.verify_pin("alice", "654321", account_store.get_pin_hash("alice"))


def test_deleting_needs_the_pin(client):
    holding_store.upsert("alice", "KR:005930", qty=1, avg_cost=1)
    assert client.request("DELETE", "/api/accounts/alice", json={"pin": "000000"},
                          cookies=_cookie()).status_code == 403
    r = client.request("DELETE", "/api/accounts/alice", json={"pin": "123456"}, cookies=_cookie())
    assert r.status_code == 200 and not account_store.get("alice")
    assert not holding_store.list_by_account("alice")      # cascade, as before
    assert account_store.get("bob")


def test_lock_clears_this_devices_cookie_without_a_session(client):
    r = client.post("/api/accounts/alice/lock")
    assert r.status_code == 200
    assert "pdash_a_alice=" in r.headers.get("set-cookie", "")


def test_the_menu_lists_only_what_this_device_has_open(client):
    assert client.get("/api/accounts/session").json() == {"open": []}
    r = client.get("/api/accounts/session", cookies=_cookie("bob")).json()
    assert [a["account_id"] for a in r["open"]] == ["bob"]


def test_the_add_dialog_cannot_overwrite_a_held_row(client):
    holding_store.upsert("alice", "KR:005930", qty=7, avg_cost=1)
    r = client.post("/api/accounts/alice/holdings",
                    json={"symbol_id": "KR:005930", "create_only": True}, cookies=_cookie())
    assert r.status_code == 409
    assert holding_store.list_by_account("alice")[0]["qty"] == 7
