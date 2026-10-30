import unittest

from sanour_ai.models.base import ModelCall, ModelProvider, ProviderError
from sanour_ai.providers.mock import TEST_FAKE_SECRET, MockBehavior, MockProvider

CALL = ModelCall(model_id="mock-echo-1", prompt="same input", max_output_tokens=16)


class MockProviderTests(unittest.TestCase):
    def test_implements_provider_protocol(self):
        self.assertIsInstance(MockProvider(), ModelProvider)

    def test_echo_is_deterministic(self):
        provider = MockProvider()
        first = provider.generate(CALL)
        second = provider.generate(CALL)
        self.assertEqual(first, second)
        self.assertEqual(provider.call_count, 2)

    def test_retryable_failure(self):
        with self.assertRaises(ProviderError) as ctx:
            MockProvider(behavior=MockBehavior.FAIL_RETRYABLE).generate(CALL)
        self.assertTrue(ctx.exception.retryable)

    def test_secret_failure_is_internal_detail_only(self):
        with self.assertRaises(ProviderError) as ctx:
            MockProvider(behavior=MockBehavior.FAIL_WITH_SECRET).generate(CALL)
        self.assertFalse(ctx.exception.retryable)
        self.assertIn(TEST_FAKE_SECRET, str(ctx.exception))

    def test_empty_behavior_returns_blank_text(self):
        result = MockProvider(behavior=MockBehavior.EMPTY).generate(CALL)
        self.assertEqual(result.text.strip(), "")
