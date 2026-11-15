import logging
import unittest

from sanour_ai.security.redaction import REDACTED, RedactingFilter, redact


class RedactionTests(unittest.TestCase):
    def test_openai_style_key_redacted(self):
        self.assertEqual(redact("key sk-ABCDEFGH12345678 end"), f"key {REDACTED} end")

    def test_key_value_pairs_redacted(self):
        self.assertEqual(redact("api_key=abc123"), f"api_key={REDACTED}")
        self.assertEqual(redact("password: hunter2"), f"password: {REDACTED}")

    def test_bearer_token_redacted(self):
        self.assertEqual(redact("Authorization: Bearer abc.def"), f"Authorization: {REDACTED}")

    def test_ordinary_text_untouched(self):
        text = "risk-analysis and task-list, tokens per second"
        self.assertEqual(redact(text), text)

    def test_filter_redacts_record_and_drops_traceback(self):
        record = logging.LogRecord(
            "t", logging.INFO, __file__, 1, "leak %s", ("sk-LEAKEDKEY12345",), None
        )
        try:
            raise RuntimeError("sk-TRACEKEY12345678")
        except RuntimeError:
            import sys

            record.exc_info = sys.exc_info()
        self.assertTrue(RedactingFilter().filter(record))
        self.assertNotIn("sk-", record.getMessage())
        self.assertIsNone(record.exc_info)
