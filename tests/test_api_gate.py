"""Global /api gate: only the PIN session cookie opens market/analysis data.

Guards the security boundary that replaced nginx Basic Auth — without this
gate /api/analysis/*/run is world-callable and spends LLM budget.
"""
import unittest

from fastapi.testclient import TestClient

from src.core import security
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
        ]:
            r = self.c.request(method, path)
            self.assertEqual(r.status_code, 403, f"{method} {path} not gated")

    def test_open_entry_points(self):
        # needed before you own a cookie: account list, landing news, PIN post,
        # and account creation (the new account's own PIN is set in the form)
        for path in ("/api/accounts", "/api/analysis/news/feed"):
            self.assertNotEqual(self.c.get(path).status_code, 403, path)
        r = self.c.post("/api/accounts/nope/auth", json={"pin": "000000"})
        self.assertEqual(r.status_code, 404)   # reached the route, not gated
        r = self.c.post("/api/accounts", json={})
        self.assertEqual(r.status_code, 422)   # reached validation, not gated

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


class TestQuantSessionOnSharedData(unittest.TestCase):
    """A quant login is a login too: its cookie opens the shared data but
    never the portfolio ledger. The token '{account}|{issued}|{sig}' names
    its own account and the signature covers it, so the gate needs no DB."""

    def setUp(self):
        from src.api.quant import COOKIE_NAME
        self.c = TestClient(app)
        self.q = {COOKIE_NAME: security.make_session_token("robo")}

    def test_opens_shared_data(self):
        for path in ("/api/analysis/summary?symbols=KR:005930",
                     "/api/universe/search?q=zzz"):
            r = self.c.get(path, cookies=self.q)
            self.assertNotEqual(r.status_code, 403, path)

    def test_still_refused_on_the_ledger(self):
        """Exactly 403 (ops notes §10) — on an account that exists and has a
        PIN, so a 404 could not stand in for the refusal."""
        import tempfile
        from unittest import mock
        from src.portfolio.store import account_store, db as pdb
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(pdb, "DB_PATH", f"{tmp}/portfolio.db"), \
                mock.patch.object(pdb, "_initialized", False):
            account_store.create("junisa", "junisa", "GENERAL")
            account_store.set_pin_hash("junisa", security.hash_pin("junisa", "123456"))
            r = self.c.get("/api/accounts/junisa", cookies=self.q)
            self.assertEqual(r.status_code, 403)
            # and the very same account opens to its own session
            name, value = (security.COOKIE_PREFIX + "junisa",
                           security.make_session_token("junisa"))
            self.assertEqual(self.c.get("/api/accounts/junisa",
                                        cookies={name: value}).status_code, 200)

    def test_refused_on_a_pin_less_ledger_account_too(self):
        """require_account opens a PIN-less account to anyone who passes the
        gate, so the quant token must not be what passes it for /api/accounts."""
        import tempfile
        from unittest import mock
        from src.portfolio.store import account_store, db as pdb
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(pdb, "DB_PATH", f"{tmp}/portfolio.db"), \
                mock.patch.object(pdb, "_initialized", False):
            account_store.create("nopin", "nopin", "GENERAL")
            # the quant token as itself, and re-labelled as a portfolio cookie
            # (tokens are not bound to the cookie name — the ledger lock is
            # require_account, which is closed while the book has no PIN)
            relabelled = {security.COOKIE_PREFIX + "robo": self.q[next(iter(self.q))]}
            for cookies in (self.q, relabelled):
                for method, path in (("GET", "/api/accounts/nopin"),
                                     ("GET", "/api/accounts/nopin/trades"),
                                     ("POST", "/api/accounts/nopin/holdings")):
                    r = self.c.request(method, path, cookies=cookies, json={})
                    self.assertEqual(r.status_code, 403, f"{method} {path}")
            # and auth on it issues nothing
            r = self.c.post("/api/accounts/nopin/auth", json={"pin": "123456"})
            self.assertEqual(r.status_code, 403)
            self.assertNotIn(security.COOKIE_PREFIX + "nopin", r.cookies)
            # a PIN is mandatory at creation, so such a row never comes from the API
            r = self.c.post("/api/accounts", json={"account_id": "x", "name": "x",
                                                   "account_type": "GENERAL"})
            self.assertEqual(r.status_code, 422)

    def test_swapped_account_is_a_forgery(self):
        from src.api.quant import COOKIE_NAME
        acct, issued, sig = security.make_session_token("robo").split("|")
        forged = {COOKIE_NAME: f"junisa|{issued}|{sig}"}
        r = self.c.get("/api/analysis/summary?symbols=KR:005930", cookies=forged)
        self.assertEqual(r.status_code, 403)
        self.assertFalse(security.token_is_valid(f"junisa|{issued}|{sig}"))
        self.assertTrue(security.token_is_valid(f"{acct}|{issued}|{sig}"))
        self.assertFalse(security.token_is_valid(None))
        self.assertFalse(security.token_is_valid("garbage"))
