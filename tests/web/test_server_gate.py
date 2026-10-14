"""Global /api gate: only the PIN session cookie opens market/analysis data.

Guards the security boundary that replaced nginx Basic Auth — without this
gate /api/analysis/*/run is world-callable and spends LLM budget.
"""
import unittest

from fastapi.testclient import TestClient

from src.portfolio import security
from src.dashboard_web.server import app


class TestApiGate(unittest.TestCase):
    def setUp(self):
        self.c = TestClient(app)
        self.cookie = (security.COOKIE_PREFIX + "junisa",
                       security.make_session_token("junisa"))

    def test_closed_without_cookie(self):
        for method, path in [
            ("GET", "/api/market/quotes?symbols=KR:005930"),
            ("GET", "/api/analysis/summary"),
            ("POST", "/api/analysis/analysis/KR/005930/run"),
            ("POST", "/api/accounts/junisa/chat"),
            ("GET", "/api/universe/search?q=samsung"),
            ("GET", "/api/health/sources"),
        ]:
            r = self.c.request(method, path)
            self.assertEqual(r.status_code, 403, f"{method} {path} not gated")

    def test_open_entry_points(self):
        # needed before you own a cookie: account list, news page feed, PIN post,
        # and account creation (the new account's own PIN is set in the form)
        for path in ("/api/accounts", "/api/analysis/news/feed"):
            self.assertNotEqual(self.c.get(path).status_code, 403, path)
        r = self.c.post("/api/accounts/nope/auth", json={"pin": "000000"})
        self.assertEqual(r.status_code, 404)   # reached the route, not gated
        r = self.c.post("/api/accounts", json={})
        self.assertEqual(r.status_code, 422)   # reached validation, not gated

    def test_redesign_open_reads(self):
        """The field grid and panel (no PII), this device's open accounts
        (empty without a cookie) and its own lock need no session."""
        for path in ("/api/analysis/news/themes", "/api/analysis/news/themes/macro",
                     "/api/accounts/session"):
            self.assertNotEqual(self.c.get(path).status_code, 403, path)
        self.assertEqual(self.c.post("/api/accounts/junisa/lock").status_code, 200)
        # …but nothing else under those prefixes opens
        self.assertEqual(self.c.post("/api/analysis/news/themes/macro").status_code, 403)
        self.assertEqual(self.c.get("/api/accounts/junisa/lock").status_code, 403)

    def test_cookie_opens_shared_data(self):
        name, value = self.cookie
        r = self.c.get("/api/analysis/summary?symbols=KR:005930",
                       cookies={name: value})
        self.assertNotEqual(r.status_code, 403)

    def test_forged_cookie_rejected(self):
        r = self.c.get("/api/analysis/summary?symbols=KR:005930",
                       cookies={security.COOKIE_PREFIX + "junisa": "junisa|1|deadbeef"})
        self.assertEqual(r.status_code, 403)

    def test_expired_token_rejected(self):
        old = security.make_session_token("junisa").split("|")
        stale = f"junisa|{int(old[1]) - security.SESSION_TTL - 60}"
        import hashlib, hmac
        sig = hmac.new(security._secret(), stale.encode(), hashlib.sha256).hexdigest()
        self.assertFalse(security.verify_session_token("junisa", f"{stale}|{sig}"))


if __name__ == "__main__":
    unittest.main()


class TestAPinLessAccountIsClosed(unittest.TestCase):
    """require_account opens a PIN-less account to anyone who passes the gate,
    so another account's session must not be what opens its ledger — as
    itself or re-labelled under the PIN-less account's cookie name (tokens
    are signed per account; the ledger lock is require_account, which is
    closed while the account has no PIN)."""

    def test_refused_on_a_pin_less_ledger_account(self):
        import tempfile
        from unittest import mock
        from src.portfolio.store import account_store, db as pdb
        c = TestClient(app)
        other = security.make_session_token("junisa")
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(pdb, "DB_PATH", f"{tmp}/portfolio.db"), \
                mock.patch.object(pdb, "_initialized", False):
            account_store.create("nopin", "nopin", "GENERAL")
            for cookies in ({security.COOKIE_PREFIX + "junisa": other},
                            {security.COOKIE_PREFIX + "nopin": other}):
                for method, path in (("GET", "/api/accounts/nopin"),
                                     ("GET", "/api/accounts/nopin/trades"),
                                     ("POST", "/api/accounts/nopin/holdings")):
                    r = c.request(method, path, cookies=cookies, json={})
                    self.assertEqual(r.status_code, 403, f"{method} {path}")
            # and auth on it issues nothing
            r = c.post("/api/accounts/nopin/auth", json={"pin": "123456"})
            self.assertEqual(r.status_code, 403)
            self.assertNotIn(security.COOKIE_PREFIX + "nopin", r.cookies)
            # a PIN is mandatory at creation, so such a row never comes from the API
            r = c.post("/api/accounts", json={"account_id": "x", "name": "x",
                                              "account_type": "GENERAL"})
            self.assertEqual(r.status_code, 422)


class TestTheHumanBookHasNoPlanRoutes(unittest.TestCase):
    """The human book keeps no plans: the plan and plan-pass routes are gone,
    not merely gated."""

    def test_plan_routes_do_not_exist(self):
        paths = {getattr(r, "path", "") for r in app.routes}
        assert not [p for p in paths if "/plans" in p and p.startswith("/api/accounts")]
        assert not [p for p in paths if "/plan_runs" in p]


def test_sources_page_is_served_and_its_data_opens_with_any_session(tmp_path, monkeypatch):
    """The page itself is a shell anyone can load; the report behind it needs
    an account session, like every /api route but the three entry points."""
    from src.core import paths
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path)
    c = TestClient(app)
    page = c.get("/sources")
    assert page.status_code == 200 and "소스 현황" in page.text
    assert "nonce-" in page.headers.get("content-security-policy", "")
    c.cookies.set(security.COOKIE_PREFIX + "junisa", security.make_session_token("junisa"))
    r = c.get("/api/health/sources")
    assert r.status_code == 200
    assert {"news", "pipeline", "market", "universe", "jobs"} <= set(r.json())
