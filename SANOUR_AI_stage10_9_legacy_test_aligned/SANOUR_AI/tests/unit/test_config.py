import unittest

from pydantic import ValidationError

from sanour_ai.config.settings import (
    ENV_MAX_CONCURRENT_PROVIDER_CALLS,
    ConfigError,
    RuntimeSettings,
    load_settings,
)

KEY = ENV_MAX_CONCURRENT_PROVIDER_CALLS


class SettingsTests(unittest.TestCase):
    def test_defaults_when_unset(self):
        self.assertEqual(load_settings({}).max_concurrent_provider_calls, 4)

    def test_valid_value_is_read(self):
        self.assertEqual(load_settings({KEY: "8"}).max_concurrent_provider_calls, 8)

    def test_surrounding_whitespace_is_tolerated(self):
        self.assertEqual(load_settings({KEY: " 3 "}).max_concurrent_provider_calls, 3)

    def test_non_integer_rejected_without_echoing_value(self):
        with self.assertRaises(ConfigError) as ctx:
            load_settings({KEY: "lots-of-secret-text"})
        self.assertIn(KEY, str(ctx.exception))
        self.assertNotIn("secret-text", str(ctx.exception))

    def test_out_of_range_values_rejected(self):
        for raw in ("0", "-1", "17"):
            with self.subTest(raw=raw):
                with self.assertRaises(ConfigError):
                    load_settings({KEY: raw})

    def test_settings_are_immutable(self):
        settings = RuntimeSettings()
        with self.assertRaises(ValidationError):
            settings.max_concurrent_provider_calls = 9

    def test_unknown_setting_rejected(self):
        with self.assertRaises(ValidationError):
            RuntimeSettings(provider_api_key="x")
