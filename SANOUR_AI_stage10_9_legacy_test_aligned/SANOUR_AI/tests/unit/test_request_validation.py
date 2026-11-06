import unittest

from sanour_ai.core.request_manager import accept
from sanour_ai.errors import ErrorCode, SanourError
from sanour_ai.models.requests import MAX_PROMPT_CHARS, SanourRequest

RID = "a" * 32


class RequestValidationTests(unittest.TestCase):
    def _rejected(self, raw: object) -> SanourError:
        with self.assertRaises(SanourError) as ctx:
            accept(raw, RID)
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_REQUEST)
        self.assertFalse(ctx.exception.retryable)
        return ctx.exception

    def test_minimal_request_gets_defaults(self):
        req = accept({"prompt": "hello"}, RID)
        self.assertIsInstance(req, SanourRequest)
        self.assertEqual(req.request_id, RID)
        self.assertEqual(req.max_output_tokens, 512)
        self.assertEqual(req.timeout_seconds, 10.0)
        self.assertFalse(req.offline_only)
        self.assertEqual(req.task_type, "general_text")

    def test_missing_prompt_rejected(self):
        self._rejected({})

    def test_empty_prompt_rejected(self):
        self._rejected({"prompt": ""})

    def test_blank_prompt_rejected(self):
        self._rejected({"prompt": "   \n  "})

    def test_oversized_prompt_rejected(self):
        self._rejected({"prompt": "x" * (MAX_PROMPT_CHARS + 1)})

    def test_control_characters_rejected(self):
        self._rejected({"prompt": "hi\x00there"})

    def test_newlines_and_tabs_allowed(self):
        accept({"prompt": "line one\n\tline two"}, RID)

    def test_non_mapping_rejected(self):
        self._rejected("just a string")

    def test_output_token_bounds(self):
        self._rejected({"prompt": "x", "max_output_tokens": 0})
        self._rejected({"prompt": "x", "max_output_tokens": 4097})

    def test_timeout_bounds(self):
        self._rejected({"prompt": "x", "timeout_seconds": 0})
        self._rejected({"prompt": "x", "timeout_seconds": 61})

    def test_unknown_field_rejected(self):
        self._rejected({"prompt": "x", "unexpected": True})

    def test_client_cannot_set_request_id(self):
        self._rejected({"prompt": "x", "request_id": "b" * 32})

    def test_too_many_metadata_items_rejected(self):
        self._rejected({"prompt": "x", "metadata": {f"k{i}": "v" for i in range(21)}})

    def test_invalid_metadata_key_rejected(self):
        self._rejected({"prompt": "x", "metadata": {"bad key!": "v"}})

    def test_long_metadata_value_rejected(self):
        self._rejected({"prompt": "x", "metadata": {"k": "v" * 201}})

    def test_error_names_fields_but_not_values(self):
        secret = "sk-TESTFAKESECRET0123456789"
        exc = self._rejected({"prompt": secret, "max_output_tokens": secret})
        self.assertIn("max_output_tokens", exc.message)
        self.assertNotIn(secret, exc.message)

    def test_unknown_field_name_not_echoed(self):
        exc = self._rejected({"prompt": "x", "sk-TESTFAKESECRET0123456789": 1})
        self.assertNotIn("sk-TEST", exc.message)
        self.assertIn("unknown_field", exc.message)

    def test_allow_fallback_is_no_longer_accepted(self):
        self._rejected({"prompt": "x", "allow_fallback": True})


class RequiredCapabilitiesTests(unittest.TestCase):
    def _rejected(self, raw):
        with self.assertRaises(SanourError) as ctx:
            accept(raw, RID)
        self.assertEqual(ctx.exception.code, ErrorCode.INVALID_REQUEST)
        return ctx.exception

    def test_legacy_payload_defaults_to_no_requirements(self):
        self.assertEqual(accept({"prompt": "hello"}, RID).required_capabilities, ())

    def test_empty_list_means_no_requirements(self):
        self.assertEqual(
            accept({"prompt": "x", "required_capabilities": []}, RID).required_capabilities, ()
        )

    def test_valid_names_are_stored_as_tuple(self):
        req = accept(
            {"prompt": "x", "required_capabilities": ["text_generation", "streaming"]}, RID
        )
        self.assertEqual(req.required_capabilities, ("text_generation", "streaming"))

    def test_unknown_name_rejected_without_echoing_value(self):
        exc = self._rejected({"prompt": "x", "required_capabilities": ["sk-FAKESECRETVALUE1"]})
        self.assertIn("required_capabilities", exc.message)
        self.assertNotIn("sk-FAKE", exc.message)

    def test_case_and_whitespace_variants_rejected(self):
        self._rejected({"prompt": "x", "required_capabilities": ["Streaming"]})
        self._rejected({"prompt": "x", "required_capabilities": [" streaming"]})

    def test_duplicate_names_rejected(self):
        self._rejected({"prompt": "x", "required_capabilities": ["streaming", "streaming"]})

    def test_non_list_value_rejected(self):
        self._rejected({"prompt": "x", "required_capabilities": "streaming"})

    def test_capability_names_match_model_capabilities_flags(self):
        from typing import get_args

        from pydantic.fields import FieldInfo

        from sanour_ai.models.capabilities import ModelCapabilities
        from sanour_ai.models.requests import CapabilityName

        flags = {
            name
            for name, info in ModelCapabilities.model_fields.items()
            if isinstance(info, FieldInfo) and info.annotation is bool
        }
        self.assertEqual(set(get_args(CapabilityName)), flags)
