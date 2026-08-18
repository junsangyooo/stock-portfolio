"""LocalLlamaBackend + local_or_claude fallback.

The fallback is the whole reason the news batch can run on an unmanaged local
server: if it dies mid-run the night's digest must still be produced, not lost.
"""
import io
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

    def test_http_error_is_not_reported_as_unreachable(self):
        # llama.cpp 500s on a generation the chat parser rejects. HTTPError
        # subclasses URLError, so the generic branch mislabelled it and sent
        # the reader after a network fault that never happened.
        err = LB.urllib.error.HTTPError(
            "http://x/v1/chat/completions", 500, "Internal Server Error", {},
            io.BytesIO(b'{"error":{"message":"does not match expected format"}}'))
        with patch.object(LB.urllib.request, "urlopen", side_effect=err):
            with self.assertRaises(RuntimeError) as cm:
                LB.LocalLlamaBackend().call("s", "u")
        msg = str(cm.exception)
        self.assertIn("HTTP 500", msg)
        self.assertIn("expected format", msg)
        self.assertNotIn("unreachable", msg)

    def test_replacement_char_is_rejected(self):
        # A quantised 4B writing Korean occasionally emits an invalid UTF-8
        # sequence. `"nov\ufffdlty": true` still parses as JSON, so nothing
        # downstream notices — the field just silently goes missing.
        payload = {"choices": [{"message": {"content": '{"nov\ufffdlty": true}'}}]}
        with patch.object(LB.urllib.request, "urlopen", return_value=_Resp(payload)):
            with self.assertRaises(RuntimeError) as cm:
                LB.LocalLlamaBackend().call("s", "u")
        self.assertIn("U+FFFD", str(cm.exception))

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

    def test_retries_local_once_before_falling_back(self):
        # The one observed fallback was a 500 on a bad generation — a retry
        # would have kept it local.
        with patch.object(LB.LocalLlamaBackend, "call",
                          side_effect=[RuntimeError("local llama HTTP 500"), "LOCAL"]) as m, \
             patch.object(LB.ClaudeCliBackend, "__init__", return_value=None), \
             patch.object(LB.ClaudeCliBackend, "call", return_value="CLAUDE"):
            self.assertEqual(LB.local_or_claude("s", "u", claude_model="m"),
                             ("LOCAL", "local_llama"))
        self.assertEqual(m.call_count, 2)

    def test_timeout_is_not_retried(self):
        # A wedged slot means a second 30-min wait, which would push the news
        # batch into the 21:10 fundamental job.
        with patch.object(LB.LocalLlamaBackend, "call",
                          side_effect=RuntimeError("local llama timeout (1800s)")) as m, \
             patch.object(LB.ClaudeCliBackend, "__init__", return_value=None), \
             patch.object(LB.ClaudeCliBackend, "call", return_value="CLAUDE"):
            self.assertEqual(LB.local_or_claude("s", "u", claude_model="m"),
                             ("CLAUDE", "claude_cli"))
        self.assertEqual(m.call_count, 1)

    def test_fallback_log_carries_stage_and_timestamp(self):
        # The state stage discards the returned backend name, so this log line
        # is the only record of which theme fell back.
        LB._get_log()
        with patch.object(LB.LocalLlamaBackend, "call", side_effect=RuntimeError("boom")), \
             patch.object(LB.ClaudeCliBackend, "__init__", return_value=None), \
             patch.object(LB.ClaudeCliBackend, "call", return_value="CLAUDE"):
            with self.assertLogs("llm", level="WARNING") as cap:
                LB.local_or_claude("s", "u", claude_model="m", stage="state:battery")
        blob = "\n".join(cap.output)
        self.assertIn("state:battery", blob)
        self.assertIn("boom", blob)
        self.assertIn("claude_cli", blob)

    def test_unparseable_local_output_retries_then_falls_back(self):
        # Validation lives inside the fallback, not in the caller: a caller-side
        # parse failure has no retry and no Claude behind it, so the theme is
        # lost for the day.
        with patch.object(LB.LocalLlamaBackend, "call", return_value="not json") as m, \
             patch.object(LB.ClaudeCliBackend, "__init__", return_value=None), \
             patch.object(LB.ClaudeCliBackend, "call", return_value='{"ok": 1}'):
            out, who = LB.local_or_claude("s", "u", claude_model="m",
                                          validate=LB._parse_json)
        self.assertEqual((out, who), ('{"ok": 1}', "claude_cli"))
        self.assertEqual(m.call_count, 2)

    def test_valid_local_output_passes_validation(self):
        with patch.object(LB.LocalLlamaBackend, "call", return_value='  {"ok": 1}  '), \
             patch.object(LB.ClaudeCliBackend, "__init__", return_value=None), \
             patch.object(LB.ClaudeCliBackend, "call", return_value="UNUSED"):
            self.assertEqual(
                LB.local_or_claude("s", "u", claude_model="m", validate=LB._parse_json),
                ('  {"ok": 1}  ', "local_llama"))

    def test_claude_failure_propagates(self):
        # both down -> the caller must see it, not a silent empty digest
        with patch.object(LB.LocalLlamaBackend, "call", side_effect=RuntimeError("down")), \
             patch.object(LB.ClaudeCliBackend, "__init__", return_value=None), \
             patch.object(LB.ClaudeCliBackend, "call", side_effect=RuntimeError("auth")):
            with self.assertRaises(RuntimeError):
                LB.local_or_claude("s", "u", claude_model="m")


if __name__ == "__main__":
    unittest.main()
