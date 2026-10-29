import os
import unittest
from unittest import mock

from sanour_ai.config.settings import (
    ENV_MAX_CONCURRENT_PROVIDER_CALLS,
    ConfigError,
    RuntimeSettings,
)
from sanour_ai.core.sanour_core import build_mock_core
from sanour_ai.errors import ErrorCode
from sanour_ai.providers.mock import MockBehavior
from tests.support import wait_until

KEY = ENV_MAX_CONCURRENT_PROVIDER_CALLS


def _without_env_key():
    env = dict(os.environ)
    env.pop(KEY, None)
    return mock.patch.dict(os.environ, env, clear=True)


class CompositionRootTests(unittest.TestCase):
    def test_environment_value_sets_gateway_cap(self):
        with mock.patch.dict(os.environ, {KEY: "2"}):
            core = build_mock_core()
        self.assertEqual(core._gateway.max_concurrent_calls, 2)

    def test_environment_value_limits_real_concurrency(self):
        # Slow mock + tiny deadlines: the first two calls time out and keep running,
        # so the third must be rejected by the cap configured from the environment.
        with mock.patch.dict(os.environ, {KEY: "2"}):
            core = build_mock_core(behavior=MockBehavior.SLOW, delay_seconds=1.0)
        codes = [
            core.handle({"prompt": "hi", "timeout_seconds": 0.05}).error.code for _ in range(3)
        ]
        self.assertEqual(
            codes,
            [
                ErrorCode.PROVIDER_TIMEOUT,
                ErrorCode.PROVIDER_TIMEOUT,
                ErrorCode.PROVIDER_OVERLOADED,
            ],
        )
        self.assertTrue(wait_until(lambda: core._gateway.in_flight == 0, timeout=3.0))

    def test_default_cap_when_environment_unset(self):
        with _without_env_key():
            core = build_mock_core()
        self.assertEqual(core._gateway.max_concurrent_calls, 4)

    def test_invalid_environment_fails_safely_at_startup(self):
        for raw in ("0", "17", "not-a-number-SECRETVALUE"):
            with self.subTest(raw=raw):
                with mock.patch.dict(os.environ, {KEY: raw}):
                    with self.assertRaises(ConfigError) as ctx:
                        build_mock_core()
                self.assertIn(KEY, str(ctx.exception))
                self.assertNotIn("SECRETVALUE", str(ctx.exception))
                self.assertNotIn(raw, str(ctx.exception).replace(KEY, ""))

    def test_explicit_settings_override_environment(self):
        with mock.patch.dict(os.environ, {KEY: "2"}):
            core = build_mock_core(RuntimeSettings(max_concurrent_provider_calls=5))
        self.assertEqual(core._gateway.max_concurrent_calls, 5)

    def test_explicit_settings_bypass_invalid_environment(self):
        with mock.patch.dict(os.environ, {KEY: "invalid"}):
            core = build_mock_core(RuntimeSettings(max_concurrent_provider_calls=3))
        self.assertEqual(core._gateway.max_concurrent_calls, 3)
