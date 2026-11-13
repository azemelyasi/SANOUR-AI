"""Stage 10.2 SDK-free tests for the production composition root. Offline. No real key.

These run with or without the OpenAI SDK installed. SDK-backed success-path tests live in
tests/integration/test_production_openai_path.py.
"""

import socket
import unittest
from unittest import mock

from sanour_ai.composition import production as prod
from sanour_ai.config.settings import ConfigError
from sanour_ai.errors import ErrorCode
from sanour_ai.models.base import ModelCall, ModelResult
from sanour_ai.models.capabilities import ModelCapabilities, ModelProfile
from sanour_ai.models.requests import SanourRequest

FAKE_KEY = "sk-FAKETESTKEY0123456789ABCDEF"  # fake, test-only

VALID_ENV = {
    "SANOUR_PROVIDER": "openai",
    "SANOUR_OPENAI_MODEL_ID": "gpt-test-1",
    "SANOUR_OPENAI_MAX_CONTEXT_TOKENS": "8192",
    "SANOUR_OPENAI_MAX_OUTPUT_TOKENS": "512",
    "OPENAI_API_KEY": FAKE_KEY,
}


def _env(**changes):
    env = dict(VALID_ENV)
    for key, value in changes.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


class RecordingProvider:
    """Test-only provider. Records calls. Never returns real data."""

    name = "openai"

    def __init__(self):
        self.call_count = 0

    def generate(self, call: ModelCall) -> ModelResult:
        self.call_count += 1
        return ModelResult(text="ok", model_id=call.model_id)


def _profile(runs_locally=False, max_output=512, max_context=8192):
    return ModelProfile(
        alias="openai_primary",
        provider="openai",
        model_id="gpt-test-1",
        capabilities=ModelCapabilities(
            text_generation=True, max_context_tokens=max_context, max_output_tokens=max_output
        ),
        runs_locally=runs_locally,
    )


class OptInGateTests(unittest.TestCase):
    def test_missing_provider_switch_is_rejected(self):
        with self.assertRaises(ConfigError) as ctx:
            prod.build_production_core(_env(SANOUR_PROVIDER=None))
        self.assertIn("SANOUR_PROVIDER", str(ctx.exception))

    def test_mock_or_other_values_are_rejected_without_echo(self):
        for raw in ("mock", "anthropic", "OPENAI", " openai "):
            with self.subTest(raw=raw):
                with self.assertRaises(ConfigError) as ctx:
                    prod.build_production_core(_env(SANOUR_PROVIDER=raw))
                self.assertIn("SANOUR_PROVIDER", str(ctx.exception))
                self.assertNotIn("mock", str(ctx.exception))

    def test_blank_provider_switch_is_rejected(self):
        with self.assertRaises(ConfigError):
            prod.build_production_core(_env(SANOUR_PROVIDER="   "))


class RequiredSettingTests(unittest.TestCase):
    def test_each_required_setting_missing_raises_named_error(self):
        for name in (
            "SANOUR_OPENAI_MODEL_ID",
            "SANOUR_OPENAI_MAX_CONTEXT_TOKENS",
            "SANOUR_OPENAI_MAX_OUTPUT_TOKENS",
            "OPENAI_API_KEY",
        ):
            with self.subTest(missing=name):
                with self.assertRaises(ConfigError) as ctx:
                    prod.build_production_core(_env(**{name: None}))
                self.assertIn(name, str(ctx.exception))
                self.assertNotIn(FAKE_KEY, str(ctx.exception))

    def test_blank_values_count_as_missing(self):
        with self.assertRaises(ConfigError) as ctx:
            prod.build_production_core(_env(SANOUR_OPENAI_MODEL_ID="   "))
        self.assertIn("SANOUR_OPENAI_MODEL_ID", str(ctx.exception))


class TokenLimitValidationTests(unittest.TestCase):
    BAD_INTS = ("0", "-1", "-98765", "abc", "12x", "1.5", "1e3", "007", "+5", "1 000", "")

    def test_zero_negative_and_malformed_limits_are_rejected(self):
        for name in ("SANOUR_OPENAI_MAX_CONTEXT_TOKENS", "SANOUR_OPENAI_MAX_OUTPUT_TOKENS"):
            for raw in self.BAD_INTS:
                with self.subTest(name=name, raw=raw):
                    with self.assertRaises(ConfigError) as ctx:
                        prod.build_production_core(_env(**{name: raw}))
                    self.assertIn(name, str(ctx.exception))
                    if raw:
                        self.assertNotIn(raw, str(ctx.exception))

    def test_output_limit_above_context_limit_is_rejected(self):
        with self.assertRaises(ConfigError) as ctx:
            prod.build_production_core(
                _env(SANOUR_OPENAI_MAX_CONTEXT_TOKENS="100", SANOUR_OPENAI_MAX_OUTPUT_TOKENS="101")
            )
        self.assertIn("SANOUR_OPENAI_MAX_OUTPUT_TOKENS", str(ctx.exception))

    def test_output_limit_equal_to_context_limit_is_not_a_config_error_here(self):
        # Equal is consistent. The SDK check (below) is the only thing that can fail after this.
        with mock.patch.object(prod, "_sdk_present", return_value=False):
            with self.assertRaises(ConfigError) as ctx:
                prod.build_production_core(
                    _env(SANOUR_OPENAI_MAX_CONTEXT_TOKENS="100", SANOUR_OPENAI_MAX_OUTPUT_TOKENS="100")
                )
            self.assertNotIn("must not be greater", str(ctx.exception))


class ModelIdValidationTests(unittest.TestCase):
    def test_malformed_model_ids_are_rejected(self):
        for raw in ("gpt 4", "gpt\t4", "-gpt", "a" * 129, "gpt/4"):
            with self.subTest(raw=raw):
                with self.assertRaises(ConfigError) as ctx:
                    prod.build_production_core(_env(SANOUR_OPENAI_MODEL_ID=raw))
                self.assertIn("SANOUR_OPENAI_MODEL_ID", str(ctx.exception))


class EndpointFailClosedTests(unittest.TestCase):
    def test_blocked_endpoint_variables_fail_closed_even_when_empty(self):
        for name in ("OPENAI_BASE_URL", "OPENAI_CUSTOM_HEADERS", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID"):
            with self.subTest(name=name):
                with self.assertRaises(ConfigError) as ctx:
                    prod.build_production_core(_env(**{name: ""}))
                self.assertIn(name, str(ctx.exception))

    def test_base_url_value_is_never_echoed(self):
        with self.assertRaises(ConfigError) as ctx:
            prod.build_production_core(_env(OPENAI_BASE_URL="https://attacker.example/v1"))
        self.assertNotIn("attacker.example", str(ctx.exception))


class RuntimeSettingTests(unittest.TestCase):
    def test_invalid_concurrency_setting_fails_at_build(self):
        with self.assertRaises(ConfigError) as ctx:
            prod.build_production_core(_env(SANOUR_MAX_CONCURRENT_PROVIDER_CALLS="99"))
        self.assertIn("SANOUR_MAX_CONCURRENT_PROVIDER_CALLS", str(ctx.exception))


class NoFallbackOrMockTests(unittest.TestCase):
    def test_missing_sdk_fails_closed_instead_of_falling_back(self):
        with mock.patch.object(prod, "_sdk_present", return_value=False):
            with self.assertRaises(ConfigError) as ctx:
                prod.build_production_core(_env())
        self.assertIn("OpenAI SDK", str(ctx.exception))
        self.assertNotIn(FAKE_KEY, str(ctx.exception))

    def test_production_module_imports_no_test_or_mock_code(self):
        import ast

        with open(prod.__file__, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules += [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules.append(node.module or "")
        for name in modules:
            self.assertFalse(name.startswith("tests"), name)
            self.assertNotIn("mock", name.lower(), name)

    def test_build_makes_no_socket_connection_when_it_fails_early(self):
        with mock.patch.object(socket.socket, "connect", side_effect=AssertionError("network")):
            with self.assertRaises(ConfigError):
                prod.build_production_core(_env(SANOUR_OPENAI_MODEL_ID=""))


class OfflineOnlyRoutingTests(unittest.TestCase):
    """Requirement 10: offline_only is enforced by the router's current rule, not by runs_locally alone."""

    def test_offline_only_request_is_refused_before_any_provider_call(self):
        provider = RecordingProvider()
        core = prod._assemble(_profile(runs_locally=False), {"openai": provider}, 4)
        raw = {"prompt": "hi", "offline_only": True}
        response = core.handle(raw)
        self.assertEqual(response.status, "error")
        self.assertEqual(response.error.code, ErrorCode.NO_ELIGIBLE_MODEL)
        self.assertEqual(provider.call_count, 0)

    def test_request_without_offline_flag_reaches_the_provider(self):
        provider = RecordingProvider()
        core = prod._assemble(_profile(runs_locally=False), {"openai": provider}, 4)
        response = core.handle({"prompt": "hi"})
        self.assertEqual(response.status, "success")
        self.assertEqual(provider.call_count, 1)

    def test_output_request_above_declared_limit_is_refused(self):
        provider = RecordingProvider()
        core = prod._assemble(_profile(max_output=64), {"openai": provider}, 4)
        response = core.handle({"prompt": "hi", "max_output_tokens": 65})
        self.assertEqual(response.error.code, ErrorCode.NO_ELIGIBLE_MODEL)
        self.assertEqual(provider.call_count, 0)


if __name__ == "__main__":
    unittest.main()
