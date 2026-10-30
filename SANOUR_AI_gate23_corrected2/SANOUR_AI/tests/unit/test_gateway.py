import time
import unittest

from sanour_ai.errors import ErrorCode, SanourError
from sanour_ai.models.base import ModelCall, ModelResult, ProviderError
from sanour_ai.models.capabilities import ModelCapabilities, ModelProfile
from sanour_ai.models.gateway import MAX_RESULT_CHARS, ModelGateway
from sanour_ai.providers.mock import MockBehavior, MockProvider
from tests.support import TOLERANCE_SECONDS, GatedProvider, deadline_in, wait_until

PROFILE = ModelProfile(
    alias="mock_default",
    provider="mock",
    model_id="mock-echo-1",
    capabilities=ModelCapabilities(
        text_generation=True, max_context_tokens=8192, max_output_tokens=4096
    ),
    runs_locally=True,
)
CALL = ModelCall(model_id="mock-echo-1", prompt="hi", max_output_tokens=16)


class _Raising:
    name = "mock"

    def __init__(self, exc):
        self._exc = exc

    def generate(self, call):
        raise self._exc


class _Returning:
    name = "mock"

    def __init__(self, value):
        self._value = value

    def generate(self, call):
        return self._value


def gateway(provider, max_calls=4):
    return ModelGateway({"mock": provider}, max_concurrent_calls=max_calls)


class GatewayTests(unittest.TestCase):
    def test_success_returns_result(self):
        result = gateway(MockProvider()).generate(PROFILE, CALL, deadline_in(2.0))
        self.assertIsInstance(result, ModelResult)
        self.assertIn("hi", result.text)

    def test_zero_concurrency_rejected_at_construction(self):
        with self.assertRaises(ValueError):
            ModelGateway({}, max_concurrent_calls=0)

    def test_unregistered_provider_is_failure(self):
        with self.assertRaises(SanourError) as ctx:
            ModelGateway({}, max_concurrent_calls=1).generate(PROFILE, CALL, deadline_in(2.0))
        self.assertEqual(ctx.exception.code, ErrorCode.PROVIDER_FAILED)

    def test_retryable_provider_error_is_marked_retryable(self):
        gw = gateway(_Raising(ProviderError("down", retryable=True)))
        with self.assertRaises(SanourError) as ctx:
            gw.generate(PROFILE, CALL, deadline_in(2.0))
        self.assertEqual(ctx.exception.code, ErrorCode.PROVIDER_FAILED)
        self.assertTrue(ctx.exception.retryable)

    def test_fatal_provider_error_is_not_retryable(self):
        gw = gateway(_Raising(ProviderError("bad", retryable=False)))
        with self.assertRaises(SanourError) as ctx:
            gw.generate(PROFILE, CALL, deadline_in(2.0))
        self.assertFalse(ctx.exception.retryable)

    def test_unexpected_exception_is_wrapped_without_detail(self):
        gw = gateway(_Raising(RuntimeError("boom sk-TESTFAKE12345678")))
        with self.assertRaises(SanourError) as ctx:
            gw.generate(PROFILE, CALL, deadline_in(2.0))
        self.assertEqual(ctx.exception.code, ErrorCode.PROVIDER_FAILED)
        self.assertNotIn("boom", ctx.exception.message)
        self.assertNotIn("sk-", ctx.exception.message)
        self.assertIsNone(ctx.exception.__context__)

    def test_timeout_is_reported_within_tolerance(self):
        gw = gateway(MockProvider(behavior=MockBehavior.SLOW, delay_seconds=1.0))
        start = time.monotonic()
        with self.assertRaises(SanourError) as ctx:
            gw.generate(PROFILE, CALL, deadline_in(0.05))
        elapsed = time.monotonic() - start
        self.assertEqual(ctx.exception.code, ErrorCode.PROVIDER_TIMEOUT)
        self.assertTrue(ctx.exception.retryable)
        self.assertLess(elapsed, 0.05 + TOLERANCE_SECONDS)

    def test_expired_deadline_fails_before_provider_call(self):
        provider = MockProvider()
        with self.assertRaises(SanourError) as ctx:
            gateway(provider).generate(PROFILE, CALL, time.monotonic() - 1.0)
        self.assertEqual(ctx.exception.code, ErrorCode.PROVIDER_TIMEOUT)
        self.assertEqual(provider.call_count, 0)

    def test_empty_output_is_invalid_response(self):
        with self.assertRaises(SanourError) as ctx:
            gateway(MockProvider(behavior=MockBehavior.EMPTY)).generate(
                PROFILE, CALL, deadline_in(2.0)
            )
        self.assertEqual(ctx.exception.code, ErrorCode.PROVIDER_INVALID_RESPONSE)

    def test_wrong_type_is_invalid_response(self):
        with self.assertRaises(SanourError) as ctx:
            gateway(_Returning("plain string")).generate(PROFILE, CALL, deadline_in(2.0))
        self.assertEqual(ctx.exception.code, ErrorCode.PROVIDER_INVALID_RESPONSE)

    def test_oversized_output_is_invalid_response(self):
        big = ModelResult(text="x" * (MAX_RESULT_CHARS + 1), model_id="mock-echo-1")
        with self.assertRaises(SanourError) as ctx:
            gateway(_Returning(big)).generate(PROFILE, CALL, deadline_in(2.0))
        self.assertEqual(ctx.exception.code, ErrorCode.PROVIDER_INVALID_RESPONSE)


class WorkerLimitTests(unittest.TestCase):
    def test_repeated_timeouts_never_exceed_the_cap(self):
        provider = GatedProvider()
        self.addCleanup(provider.release_all)
        gw = gateway(provider, max_calls=2)

        codes = []
        for _ in range(5):
            try:
                gw.generate(PROFILE, CALL, deadline_in(0.05))
                codes.append("ok")
            except SanourError as exc:
                codes.append(exc.code)

        self.assertEqual(
            codes,
            [
                ErrorCode.PROVIDER_TIMEOUT,
                ErrorCode.PROVIDER_TIMEOUT,
                ErrorCode.PROVIDER_OVERLOADED,
                ErrorCode.PROVIDER_OVERLOADED,
                ErrorCode.PROVIDER_OVERLOADED,
            ],
        )
        self.assertTrue(wait_until(lambda: provider.calls == 2))
        self.assertEqual(provider.peak, 2)
        self.assertEqual(gw.in_flight, 2)
        self.assertEqual(gw.timeouts, 2)

    def test_overflow_is_rejected_without_starting_a_worker(self):
        provider = GatedProvider()
        self.addCleanup(provider.release_all)
        gw = gateway(provider, max_calls=1)
        with self.assertRaises(SanourError):
            gw.generate(PROFILE, CALL, deadline_in(0.05))
        self.assertTrue(wait_until(lambda: provider.calls == 1))

        with self.assertRaises(SanourError) as ctx:
            gw.generate(PROFILE, CALL, deadline_in(2.0))
        self.assertEqual(ctx.exception.code, ErrorCode.PROVIDER_OVERLOADED)
        self.assertTrue(ctx.exception.retryable)
        self.assertEqual(provider.calls, 1)

    def test_slot_is_held_until_abandoned_worker_returns(self):
        provider = GatedProvider()
        self.addCleanup(provider.release_all)
        gw = gateway(provider, max_calls=1)
        with self.assertRaises(SanourError):
            gw.generate(PROFILE, CALL, deadline_in(0.05))
        self.assertTrue(wait_until(lambda: provider.calls == 1))
        self.assertEqual(gw.in_flight, 1)

        provider.release_all()
        self.assertTrue(wait_until(lambda: gw.in_flight == 0))
        result = gw.generate(PROFILE, CALL, deadline_in(2.0))
        self.assertIn("hi", result.text)
