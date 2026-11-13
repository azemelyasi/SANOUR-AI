"""Stage 10.2 SDK-backed tests: production core end to end over httpx2.MockTransport.

Offline only. No real key, no real endpoint. Skipped when openai/httpx2 are not installed.
Test-only patches: the HTTP client is replaced by a MockTransport client, and the transport
allowlist is widened for MockTransport only. Production code is unchanged.
"""

import importlib.util
import json
import os
import socket
import unittest
from unittest import mock

from sanour_ai.composition import production as prod
from sanour_ai.errors import ErrorCode

FAKE_KEY = "sk-FAKETESTKEY0123456789ABCDEF"  # fake, test-only
MODEL = "gpt-test-1"


def _sdk_available():
    return (
        importlib.util.find_spec("openai") is not None
        and importlib.util.find_spec("httpx2") is not None
    )


SDK_REQUIRED = unittest.skipUnless(_sdk_available(), "openai and httpx2 not installed; skipped")

ENV = {
    "SANOUR_PROVIDER": "openai",
    "SANOUR_OPENAI_MODEL_ID": MODEL,
    "SANOUR_OPENAI_MAX_CONTEXT_TOKENS": "8192",
    "SANOUR_OPENAI_MAX_OUTPUT_TOKENS": "512",
    "OPENAI_API_KEY": FAKE_KEY,
}


def _ok_body(model=MODEL, text="hello"):
    return {
        "id": "resp_1", "object": "response", "created_at": 0, "status": "completed",
        "model": model,
        "output": [{"type": "message", "id": "m1", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": text, "annotations": []}]}],
        "parallel_tool_calls": False, "tool_choice": "auto", "tools": [],
    }


@SDK_REQUIRED
class ProductionOpenAIPathTests(unittest.TestCase):
    def setUp(self):
        import httpx2

        from sanour_ai.providers import openai_provider as op

        self._httpx2 = httpx2
        self.requests = []
        self.reply = lambda request: httpx2.Response(200, json=_ok_body())
        patches = [
            mock.patch.dict(os.environ, {}, clear=False),
            mock.patch.object(
                op,
                "_trusted_transport_types",
                return_value=(httpx2.HTTPTransport, httpx2.MockTransport),
            ),
            mock.patch.object(
                op,
                "_build_http_client",
                side_effect=lambda: httpx2.Client(
                    transport=httpx2.MockTransport(self._handler), trust_env=False
                ),
            ),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def _handler(self, request):
        self.requests.append(request)
        return self.reply(request)

    def _build(self, env=ENV):
        return prod.build_production_core(env)

    def test_build_sends_no_request(self):
        with mock.patch.object(socket.socket, "connect", side_effect=AssertionError("network")):
            core = self._build()
        self.assertEqual(self.requests, [])
        self.assertIsNotNone(core)

    def test_success_makes_exactly_one_request_with_store_false(self):
        core = self._build()
        response = core.handle({"prompt": "hi"})
        self.assertEqual(response.status, "success")
        self.assertEqual(response.output, "hello")
        self.assertEqual(len(self.requests), 1)
        payload = json.loads(self.requests[0].content)
        self.assertEqual(payload["model"], MODEL)
        self.assertIs(payload["store"], False)

    def test_model_mismatch_is_invalid_response(self):
        self.reply = lambda request: self._httpx2.Response(200, json=_ok_body(model="other-model"))
        response = self._build().handle({"prompt": "hi"})
        self.assertEqual(response.error.code, ErrorCode.PROVIDER_INVALID_RESPONSE)
        self.assertEqual(len(self.requests), 1)

    def test_rate_limit_is_reported_retryable_with_no_retry(self):
        self.reply = lambda request: self._httpx2.Response(429, json={"error": {"message": "slow"}})
        response = self._build().handle({"prompt": "hi"})
        self.assertEqual(response.error.code, ErrorCode.PROVIDER_FAILED)
        self.assertTrue(response.error.retryable)
        self.assertEqual(len(self.requests), 1)

    def test_key_never_appears_in_response_or_audit(self):
        with self.assertLogs("sanour_ai.audit", level="INFO") as logs:
            response = self._build().handle({"prompt": "hi"})
        self.assertNotIn(FAKE_KEY, response.model_dump_json())
        self.assertNotIn(FAKE_KEY, "\n".join(logs.output))

    def test_offline_only_request_never_reaches_the_wire(self):
        response = self._build().handle({"prompt": "hi", "offline_only": True})
        self.assertEqual(response.error.code, ErrorCode.NO_ELIGIBLE_MODEL)
        self.assertEqual(self.requests, [])

    def test_output_request_above_declared_limit_never_reaches_the_wire(self):
        response = self._build().handle({"prompt": "hi", "max_output_tokens": 513})
        self.assertEqual(response.error.code, ErrorCode.NO_ELIGIBLE_MODEL)
        self.assertEqual(self.requests, [])


if __name__ == "__main__":
    unittest.main()
