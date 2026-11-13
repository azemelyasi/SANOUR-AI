import unittest

from pydantic import ValidationError

from sanour_ai.errors import ErrorCode, SanourError
from sanour_ai.models.responses import ErrorInfo, SanourResponse


class ResponseInvariantTests(unittest.TestCase):
    def test_success_requires_output_and_no_error(self):
        with self.assertRaises(ValidationError):
            SanourResponse(request_id="a" * 32, status="success", output=None)

    def test_error_requires_error_and_no_output(self):
        with self.assertRaises(ValidationError):
            SanourResponse(
                request_id="a" * 32,
                status="error",
                output="leaked",
                error=ErrorInfo(code=ErrorCode.INTERNAL_ERROR, message="m", retryable=False),
            )

    def test_failure_factory_copies_safe_fields(self):
        err = SanourError(ErrorCode.PROVIDER_FAILED, "model provider failed", retryable=True)
        resp = SanourResponse.failure(request_id="a" * 32, error=err, latency_ms=7)
        self.assertEqual(resp.status, "error")
        self.assertIsNone(resp.output)
        self.assertEqual(resp.error.code, ErrorCode.PROVIDER_FAILED)
        self.assertEqual(resp.error.message, "model provider failed")
        self.assertTrue(resp.error.retryable)
        self.assertEqual(resp.latency_ms, 7)
