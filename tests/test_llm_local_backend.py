"""LocalLlamaBackend + local_or_claude fallback.

The fallback is the whole reason the news batch can run on an unmanaged local
server: if it dies mid-run the night's digest must still be produced, not lost.
"""
import json
import unittest
from unittest.mock import patch

from src.agents import llm_backend as LB


class _Resp:
    def __init__(self, payload):
        self._p = json.dumps(payload).encode()

    def read(self):
        return self._p

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestLocalLlamaBackend(unittest.TestCase):
    def test_returns_message_content(self):
        payload = {"choices": [{"message": {"content": "  {\"ok\": 1}  "}}]}
        with patch.object(LB.urllib.request, "urlopen", return_value=_Resp(payload)):
            self.assertEqual(LB.LocalLlamaBackend().call("s", "u"), '{"ok": 1}')

    def test_unreachable_raises(self):
        with patch.object(LB.urllib.request, "urlopen",
                          side_effect=LB.urllib.error.URLError("refused")):
            with self.assertRaises(RuntimeError):
                LB.LocalLlamaBackend().call("s", "u")

    def test_malformed_response_raises(self):
        with patch.object(LB.urllib.request, "urlopen", return_value=_Resp({"x": 1})):
            with self.assertRaises(RuntimeError):
                LB.LocalLlamaBackend().call("s", "u")


class TestFallback(unittest.TestCase):
    def test_uses_local_when_healthy(self):
        with patch.object(LB.LocalLlamaBackend, "call", return_value="LOCAL"):
            self.assertEqual(LB.local_or_claude("s", "u", claude_model="m"),
                             ("LOCAL", "local_llama"))

    def test_falls_back_to_claude_on_any_local_failure(self):
        for exc in (RuntimeError("down"), TimeoutError(), ValueError("junk")):
            with patch.object(LB.LocalLlamaBackend, "call", side_effect=exc), \
                 patch.object(LB.ClaudeCliBackend, "__init__", return_value=None), \
                 patch.object(LB.ClaudeCliBackend, "call", return_value="CLAUDE"):
                self.assertEqual(LB.local_or_claude("s", "u", claude_model="m"),
                                 ("CLAUDE", "claude_cli"))

    def test_claude_failure_propagates(self):
        # both down -> the caller must see it, not a silent empty digest
        with patch.object(LB.LocalLlamaBackend, "call", side_effect=RuntimeError("down")), \
             patch.object(LB.ClaudeCliBackend, "__init__", return_value=None), \
             patch.object(LB.ClaudeCliBackend, "call", side_effect=RuntimeError("auth")):
            with self.assertRaises(RuntimeError):
                LB.local_or_claude("s", "u", claude_model="m")


if __name__ == "__main__":
    unittest.main()
