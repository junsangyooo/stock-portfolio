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
            ("POST", "/api/analysis/chart/KR/005930/run"),
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
