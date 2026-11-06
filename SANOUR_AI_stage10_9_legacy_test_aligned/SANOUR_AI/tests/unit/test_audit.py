import unittest

from sanour_ai.observability.audit import AuditLogger


class AuditTests(unittest.TestCase):
    def test_emits_whitelisted_fields(self):
        audit = AuditLogger()
        with self.assertLogs("sanour_ai.audit", level="INFO") as logs:
            audit.emit("model.succeeded", request_id="r1", model_alias="mock_default", latency_ms=3)
        line = logs.output[0]
        self.assertIn("model.succeeded", line)
        self.assertIn('"request_id": "r1"', line)

    def test_prompt_text_never_logged(self):
        audit = AuditLogger()
        with self.assertLogs("sanour_ai.audit", level="INFO") as logs:
            audit.emit("request.received", request_id="r2", prompt="PRIVATE PROMPT TEXT")
        self.assertNotIn("PRIVATE PROMPT TEXT", "\n".join(logs.output))

    def test_secret_in_allowed_field_is_redacted(self):
        audit = AuditLogger()
        with self.assertLogs("sanour_ai.audit", level="INFO") as logs:
            audit.emit("model.failed", model_id="sk-LEAKEDVALUE123456")
        joined = "\n".join(logs.output)
        self.assertNotIn("sk-LEAKEDVALUE", joined)
        self.assertIn("[REDACTED]", joined)
