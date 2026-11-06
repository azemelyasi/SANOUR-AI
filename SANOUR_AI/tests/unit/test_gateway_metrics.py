import logging
import threading
import time
import unittest
from unittest import mock

from pydantic import ValidationError

from sanour_ai.errors import ErrorCode, SanourError
from sanour_ai.models.base import ModelCall, ModelResult, ProviderError
from sanour_ai.models.capabilities import ModelCapabilities, ModelProfile
import sanour_ai.models.gateway as gwmod
from sanour_ai.models.gateway import GatewayStats, ModelGateway
from sanour_ai.providers.mock import MockBehavior, MockProvider
from tests.support import GatedProvider, deadline_in, wait_until

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


class _ReadsStats:
    """Calls stats() from inside generate(). Would deadlock if the lock were held."""

    name = "mock"

    def __init__(self):
        self.gateway = None
        self.seen = None

    def generate(self, call):
        self.seen = self.gateway.stats()
        return ModelResult(text="ok", model_id=call.model_id)


def gateway(provider, max_calls=4):
    return ModelGateway({"mock": provider}, max_concurrent_calls=max_calls)


def total(stats: GatewayStats) -> int:
    return (
        stats.successful_calls
        + stats.provider_failures
        + stats.timeouts
        + stats.overload_rejections
        + stats.invalid_responses
        + stats.unclassified_errors
    )


class OutcomeCounterTests(unittest.TestCase):
    def test_fresh_gateway_is_all_zero(self):
        stats = gateway(MockProvider()).stats()
        self.assertEqual(total(stats), 0)
        self.assertEqual(
            (stats.in_flight, stats.abandoned_in_flight, stats.peak_in_flight), (0, 0, 0)
        )

    def test_success_counts_once(self):
        gw = gateway(MockProvider())
        gw.generate(PROFILE, CALL, deadline_in(2.0))
        stats = gw.stats()
        self.assertEqual(stats.successful_calls, 1)
        self.assertEqual(total(stats), 1)
        self.assertEqual(stats.in_flight, 0)

    def test_provider_failures_are_counted(self):
        gw = gateway(_Raising(ProviderError("down", retryable=True)))
        with self.assertRaises(SanourError):
            gw.generate(PROFILE, CALL, deadline_in(2.0))
        gw2 = gateway(_Raising(RuntimeError("boom")))
        with self.assertRaises(SanourError):
            gw2.generate(PROFILE, CALL, deadline_in(2.0))
        unregistered = ModelGateway({}, max_concurrent_calls=1)
        with self.assertRaises(SanourError):
            unregistered.generate(PROFILE, CALL, deadline_in(2.0))
        self.assertEqual(gw.stats().provider_failures, 1)
        self.assertEqual(gw2.stats().provider_failures, 1)
        self.assertEqual(unregistered.stats().provider_failures, 1)

    def test_invalid_responses_are_counted(self):
        gw = gateway(MockProvider(behavior=MockBehavior.EMPTY))
        with self.assertRaises(SanourError):
            gw.generate(PROFILE, CALL, deadline_in(2.0))
        gw2 = gateway(_Returning("plain string"))
        with self.assertRaises(SanourError):
            gw2.generate(PROFILE, CALL, deadline_in(2.0))
        self.assertEqual(gw.stats().invalid_responses, 1)
        self.assertEqual(gw2.stats().invalid_responses, 1)

    def test_pre_start_expiry_is_a_timeout_with_no_provider_call(self):
        provider = MockProvider()
        gw = gateway(provider)
        with self.assertRaises(SanourError) as ctx:
            gw.generate(PROFILE, CALL, time.monotonic() - 1.0)
        self.assertEqual(ctx.exception.code, ErrorCode.PROVIDER_TIMEOUT)
        self.assertEqual(provider.call_count, 0)
        stats = gw.stats()
        self.assertEqual(stats.timeouts, 1)
        self.assertEqual(stats.in_flight, 0)

    def test_wait_timeout_counts_and_legacy_property_still_counts_it(self):
        gw = gateway(MockProvider(behavior=MockBehavior.SLOW, delay_seconds=0.5))
        with self.assertRaises(SanourError):
            gw.generate(PROFILE, CALL, deadline_in(0.05))
        self.assertEqual(gw.stats().timeouts, 1)
        self.assertEqual(gw.timeouts, 1)

    def test_legacy_timeouts_property_ignores_pre_start_expiry(self):
        gw = gateway(MockProvider())
        with self.assertRaises(SanourError):
            gw.generate(PROFILE, CALL, time.monotonic() - 1.0)
        self.assertEqual(gw.timeouts, 0)
        self.assertEqual(gw.stats().timeouts, 1)

    def test_overload_is_counted_and_starts_no_thread(self):
        provider = GatedProvider()
        self.addCleanup(provider.release_all)
        gw = gateway(provider, max_calls=1)
        with self.assertRaises(SanourError):
            gw.generate(PROFILE, CALL, deadline_in(0.05))
        self.assertTrue(wait_until(lambda: provider.calls == 1))
        with self.assertRaises(SanourError) as ctx:
            gw.generate(PROFILE, CALL, deadline_in(2.0))
        self.assertEqual(ctx.exception.code, ErrorCode.PROVIDER_OVERLOADED)
        stats = gw.stats()
        self.assertEqual(stats.overload_rejections, 1)
        self.assertEqual(provider.calls, 1)
        self.assertEqual(stats.in_flight, 1)
        self.assertEqual(stats.peak_in_flight, 1)

    def test_unclassified_exception_is_counted_and_reraised(self):
        gw = gateway(MockProvider())
        with mock.patch.object(gw, "_generate", side_effect=RuntimeError("unexpected")):
            with self.assertRaises(RuntimeError):
                gw.generate(PROFILE, CALL, deadline_in(2.0))
        stats = gw.stats()
        self.assertEqual(stats.unclassified_errors, 1)
        self.assertEqual(total(stats), 1)


class ConcurrencyAndAbandonmentTests(unittest.TestCase):
    def test_concurrent_requests_partition_into_outcomes(self):
        provider = MockProvider(behavior=MockBehavior.SLOW, delay_seconds=0.02)
        gw = gateway(provider, max_calls=4)
        errors = []

        def worker():
            try:
                gw.generate(PROFILE, CALL, deadline_in(2.0))
            except SanourError as exc:
                errors.append(exc.code)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        self.assertTrue(wait_until(lambda: gw.in_flight == 0))
        stats = gw.stats()
        self.assertEqual(total(stats), 20)
        self.assertEqual(stats.successful_calls + stats.overload_rejections, 20)
        self.assertEqual(len(errors), stats.overload_rejections)
        self.assertLessEqual(stats.peak_in_flight, 4)
        self.assertGreaterEqual(stats.peak_in_flight, 1)
        self.assertEqual(stats.in_flight, 0)

    def test_abandoned_worker_stays_in_flight_until_it_returns(self):
        provider = GatedProvider()
        self.addCleanup(provider.release_all)
        gw = gateway(provider, max_calls=2)
        with self.assertRaises(SanourError):
            gw.generate(PROFILE, CALL, deadline_in(0.05))
        self.assertTrue(wait_until(lambda: provider.calls == 1))
        stats = gw.stats()
        self.assertEqual((stats.in_flight, stats.abandoned_in_flight), (1, 1))
        self.assertEqual(stats.successful_calls, 0)

        provider.release_all()
        self.assertTrue(wait_until(lambda: gw.in_flight == 0))
        stats = gw.stats()
        self.assertEqual((stats.in_flight, stats.abandoned_in_flight), (0, 0))
        self.assertEqual(stats.successful_calls, 0)
        self.assertEqual(stats.timeouts, 1)

    def test_worker_finishing_after_timeout_is_not_a_success(self):
        gw = gateway(MockProvider(behavior=MockBehavior.SLOW, delay_seconds=0.2))
        with self.assertRaises(SanourError):
            gw.generate(PROFILE, CALL, deadline_in(0.05))
        self.assertTrue(wait_until(lambda: gw.in_flight == 0, timeout=3.0))
        time.sleep(0.05)
        stats = gw.stats()
        self.assertEqual(stats.successful_calls, 0)
        self.assertEqual(stats.timeouts, 1)
        self.assertEqual(stats.abandoned_in_flight, 0)


class SnapshotAndLockTests(unittest.TestCase):
    def test_snapshot_is_frozen_and_rejects_extra_fields(self):
        stats = gateway(MockProvider()).stats()
        with self.assertRaises(ValidationError):
            stats.successful_calls = 5
        with self.assertRaises(ValidationError):
            GatewayStats(**stats.model_dump(), extra_field=1)

    def test_returned_snapshot_is_unchanged_by_later_calls(self):
        gw = gateway(MockProvider())
        before = gw.stats()
        frozen_copy = before.model_dump()
        gw.generate(PROFILE, CALL, deadline_in(2.0))
        self.assertEqual(before.model_dump(), frozen_copy)
        self.assertEqual(before.successful_calls, 0)
        self.assertEqual(gw.stats().successful_calls, 1)

    def test_reading_stats_does_not_change_counters(self):
        gw = gateway(MockProvider())
        gw.generate(PROFILE, CALL, deadline_in(2.0))
        first = gw.stats()
        second = gw.stats()
        third = gw.stats()
        self.assertEqual(first, second)
        self.assertEqual(second, third)
        self.assertEqual(third.successful_calls, 1)

    def test_provider_can_read_stats_during_call_without_deadlock(self):
        provider = _ReadsStats()
        gw = gateway(provider)
        provider.gateway = gw
        worker = threading.Thread(target=gw.generate, args=(PROFILE, CALL, deadline_in(2.0)))
        worker.start()
        worker.join(timeout=3.0)
        self.assertFalse(worker.is_alive(), "stats() inside a provider call deadlocked")
        self.assertEqual(provider.seen.in_flight, 1)

    def test_stats_returns_promptly_while_provider_is_blocked(self):
        provider = GatedProvider()
        self.addCleanup(provider.release_all)
        gw = gateway(provider)
        worker = threading.Thread(target=gw.generate, args=(PROFILE, CALL, deadline_in(5.0)))
        worker.start()
        self.assertTrue(wait_until(lambda: provider.calls == 1))
        started = time.monotonic()
        stats = gw.stats()
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(stats.in_flight, 1)
        provider.release_all()
        worker.join(timeout=3.0)


_RealEvent = threading.Event


class _RaceEvent(_RealEvent):
    """Event whose timed wait() is scripted, to place a worker finish inside the deadline window."""

    def __init__(self, script):
        super().__init__()
        self._script = script

    def wait(self, timeout=None):
        if timeout is None:
            return super().wait()
        return self._script(self, timeout)


class _Fatal(BaseException):
    """Stands in for process-control exceptions such as SystemExit or KeyboardInterrupt."""


class _FatalOnce:
    name = "mock"

    def __init__(self, exc):
        self._exc = exc
        self.calls = 0

    def generate(self, call):
        self.calls += 1
        if self.calls == 1:
            raise self._exc
        return ModelResult(text="recovered", model_id=call.model_id)


class _GatedFatal:
    name = "mock"

    def __init__(self):
        self.gate = threading.Event()

    def generate(self, call):
        self.gate.wait()
        raise _Fatal("late")


class _Collect(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


class StartFailureCleanupTests(unittest.TestCase):
    def test_thread_constructor_failure_releases_slot(self):
        provider = MockProvider()
        gw = gateway(provider, max_calls=1)
        with mock.patch.object(gwmod.threading, "Thread", side_effect=MemoryError("no")):
            with self.assertRaises(SanourError) as ctx:
                gw.generate(PROFILE, CALL, deadline_in(2.0))
        self.assertEqual(ctx.exception.code, ErrorCode.PROVIDER_FAILED)
        stats = gw.stats()
        self.assertEqual(stats.in_flight, 0)
        self.assertEqual(stats.abandoned_in_flight, 0)
        self.assertEqual(stats.peak_in_flight, 0)
        self.assertEqual((stats.provider_failures, stats.unclassified_errors), (1, 0))
        self.assertEqual(provider.call_count, 0)
        # Capacity restored: a valid request on the single slot succeeds.
        self.assertEqual(gw.generate(PROFILE, CALL, deadline_in(2.0)).model_id, "mock-echo-1")

    def test_thread_start_failure_before_claim_releases_slot_once(self):
        provider = MockProvider()
        gw = gateway(provider, max_calls=1)
        with mock.patch.object(
            threading.Thread, "start", side_effect=RuntimeError("can't start new thread")
        ):
            with self.assertRaises(SanourError) as ctx:
                gw.generate(PROFILE, CALL, deadline_in(2.0))
        self.assertEqual(ctx.exception.code, ErrorCode.PROVIDER_FAILED)
        stats = gw.stats()
        self.assertEqual((stats.in_flight, stats.abandoned_in_flight), (0, 0))
        self.assertEqual(stats.peak_in_flight, 0)
        self.assertEqual(stats.provider_failures, 1)
        self.assertEqual(provider.call_count, 0)
        self.assertEqual(gw.generate(PROFILE, CALL, deadline_in(2.0)).model_id, "mock-echo-1")

    def test_start_failure_after_worker_claimed_defers_cleanup_to_worker(self):
        provider = GatedProvider()
        self.addCleanup(provider.release_all)
        gw = gateway(provider, max_calls=1)
        real_start = threading.Thread.start

        def start_then_fail(thread):
            real_start(thread)
            # Deterministic: wait until the worker has claimed the call and entered the provider.
            self.assertTrue(wait_until(lambda: provider.calls == 1))
            raise RuntimeError("late start failure")

        with mock.patch.object(threading.Thread, "start", start_then_fail):
            with self.assertRaises(SanourError) as ctx:
                gw.generate(PROFILE, CALL, deadline_in(2.0))
        self.assertEqual(ctx.exception.code, ErrorCode.PROVIDER_FAILED)
        stats = gw.stats()
        self.assertEqual((stats.in_flight, stats.abandoned_in_flight), (1, 1))
        self.assertEqual(stats.peak_in_flight, 1)

        provider.release_all()
        self.assertTrue(wait_until(lambda: gw.in_flight == 0))
        stats = gw.stats()
        self.assertEqual((stats.in_flight, stats.abandoned_in_flight), (0, 0))
        self.assertEqual(stats.provider_failures, 1)
        self.assertEqual(stats.successful_calls, 0)
        self.assertEqual(
            stats.successful_calls + stats.provider_failures + stats.timeouts
            + stats.overload_rejections + stats.invalid_responses + stats.unclassified_errors,
            1,
        )


class ConcurrentTimeoutCleanupTests(unittest.TestCase):
    def test_concurrent_caller_timeouts_then_worker_cleanup(self):
        provider = GatedProvider()
        self.addCleanup(provider.release_all)
        gw = gateway(provider, max_calls=10)
        codes = []
        codes_lock = threading.Lock()

        def caller():
            try:
                gw.generate(PROFILE, CALL, deadline_in(0.05))
            except SanourError as exc:
                with codes_lock:
                    codes.append(exc.code)

        callers = [threading.Thread(target=caller) for _ in range(10)]
        for t in callers:
            t.start()
        for t in callers:
            t.join(timeout=5)
        self.assertEqual(codes, [ErrorCode.PROVIDER_TIMEOUT] * 10)
        self.assertTrue(wait_until(lambda: provider.calls == 10))
        stats = gw.stats()
        self.assertEqual((stats.timeouts, gw.timeouts), (10, 10))
        self.assertEqual((stats.in_flight, stats.abandoned_in_flight), (10, 10))
        self.assertEqual(stats.peak_in_flight, 10)
        self.assertEqual(stats.successful_calls, 0)

        provider.release_all()
        self.assertTrue(wait_until(lambda: gw.in_flight == 0))
        stats = gw.stats()
        self.assertEqual((stats.in_flight, stats.abandoned_in_flight), (0, 0))
        self.assertEqual(stats.timeouts, 10)
        self.assertEqual(stats.successful_calls, 0)
        self.assertEqual(gw.generate(PROFILE, CALL, deadline_in(2.0)).text, "gated:hi")

    def test_worker_finishing_after_caller_timeout_is_not_counted_as_success(self):
        provider = GatedProvider()
        self.addCleanup(provider.release_all)
        gw = gateway(provider, max_calls=1)
        with self.assertRaises(SanourError):
            gw.generate(PROFILE, CALL, deadline_in(0.05))
        provider.release_all()
        self.assertTrue(wait_until(lambda: gw.in_flight == 0))
        stats = gw.stats()
        self.assertEqual((stats.successful_calls, stats.timeouts), (0, 1))
        self.assertEqual(stats.abandoned_in_flight, 0)


class BaseExceptionPolicyTests(unittest.TestCase):
    def test_provider_base_exception_propagates_and_counts_unclassified(self):
        provider = _FatalOnce(_Fatal("stop"))
        gw = gateway(provider, max_calls=1)
        with self.assertRaises(_Fatal):
            gw.generate(PROFILE, CALL, deadline_in(2.0))
        stats = gw.stats()
        self.assertEqual(stats.unclassified_errors, 1)
        self.assertEqual((stats.invalid_responses, stats.provider_failures), (0, 0))
        self.assertEqual((stats.in_flight, stats.abandoned_in_flight), (0, 0))
        self.assertEqual(gw.generate(PROFILE, CALL, deadline_in(2.0)).text, "recovered")

    def test_system_exit_from_provider_is_not_an_invalid_response(self):
        gw = gateway(_FatalOnce(SystemExit("exit")), max_calls=1)
        with self.assertRaises(SystemExit):
            gw.generate(PROFILE, CALL, deadline_in(2.0))
        stats = gw.stats()
        self.assertEqual((stats.invalid_responses, stats.unclassified_errors), (0, 1))

    def test_base_exception_after_timeout_is_logged_not_swallowed_silently(self):
        provider = _GatedFatal()
        gw = gateway(provider, max_calls=1)
        collector = _Collect()
        logger = logging.getLogger("sanour_ai.gateway")
        logger.addHandler(collector)
        self.addCleanup(logger.removeHandler, collector)
        with self.assertRaises(SanourError) as ctx:
            gw.generate(PROFILE, CALL, deadline_in(0.05))
        self.assertEqual(ctx.exception.code, ErrorCode.PROVIDER_TIMEOUT)
        provider.gate.set()
        self.assertTrue(
            wait_until(lambda: any(r.levelno == logging.WARNING for r in collector.records))
        )
        warnings = [r.getMessage() for r in collector.records if r.levelno == logging.WARNING]
        self.assertEqual(warnings, ["abandoned provider call raised _Fatal"])
        stats = gw.stats()
        self.assertEqual((stats.timeouts, stats.unclassified_errors), (1, 0))
        self.assertEqual((stats.in_flight, stats.abandoned_in_flight), (0, 0))


def _patched_finished_event(script):
    """Only the first Event created by generate() (the `finished` event) is scripted."""
    created = {"n": 0}

    def factory():
        created["n"] += 1
        return _RaceEvent(script) if created["n"] == 1 else _RealEvent()

    return mock.patch.object(gwmod.threading, "Event", factory)


class _GatedFatalOnce:
    name = "mock"

    def __init__(self):
        self.gate = threading.Event()
        self.calls = 0

    def generate(self, call):
        self.calls += 1
        if self.calls == 1:
            self.gate.wait()
            raise _Fatal("late")
        return ModelResult(text="recovered", model_id=call.model_id)


class _BrokenHandler(logging.Handler):
    def emit(self, record):
        raise RuntimeError("log sink broken")


class DeterministicRaceTests(unittest.TestCase):
    def test_completion_just_before_expiry_is_success(self):
        provider = GatedProvider()
        self.addCleanup(provider.release_all)
        gw = gateway(provider, max_calls=1)

        def script(event, timeout):
            provider.release_all()  # worker finishes inside the window
            return _RealEvent.wait(event, 5.0)

        with _patched_finished_event(script):
            result = gw.generate(PROFILE, CALL, deadline_in(5.0))
        self.assertEqual(result.text, "gated:hi")
        stats = gw.stats()
        self.assertEqual((stats.successful_calls, stats.timeouts), (1, 0))
        self.assertEqual((stats.in_flight, stats.abandoned_in_flight), (0, 0))

    def test_completion_just_after_expiry_is_timeout_and_not_abandoned(self):
        provider = GatedProvider()
        self.addCleanup(provider.release_all)
        gw = gateway(provider, max_calls=1)

        def script(event, timeout):
            _RealEvent.wait(event, timeout)  # real deadline elapses, worker still blocked
            provider.release_all()  # worker finishes after expiry, before the caller re-checks
            _RealEvent.wait(event, 5.0)  # wait until its cleanup has run
            return False

        with _patched_finished_event(script):
            with self.assertRaises(SanourError) as ctx:
                gw.generate(PROFILE, CALL, deadline_in(0.05))
        self.assertEqual(ctx.exception.code, ErrorCode.PROVIDER_TIMEOUT)
        stats = gw.stats()
        self.assertEqual((stats.timeouts, stats.successful_calls), (1, 0))
        self.assertEqual((stats.in_flight, stats.abandoned_in_flight), (0, 0))
        self.assertEqual(gw.timeouts, 1)


class LoggingFailureTests(unittest.TestCase):
    def test_logging_failure_cannot_leak_capacity(self):
        provider = _GatedFatalOnce()
        gw = gateway(provider, max_calls=1)
        logger = logging.getLogger("sanour_ai.gateway")
        handler = _BrokenHandler()
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)
        seen = []
        with mock.patch.object(threading, "excepthook", lambda args: seen.append(args.exc_type)):
            with self.assertRaises(SanourError) as ctx:
                gw.generate(PROFILE, CALL, deadline_in(0.05))
            self.assertEqual(ctx.exception.code, ErrorCode.PROVIDER_TIMEOUT)
            self.assertTrue(wait_until(lambda: provider.calls == 1))
            provider.gate.set()  # worker now raises BaseException after the caller timed out
            self.assertTrue(wait_until(lambda: len(seen) == 1))
        self.assertEqual(seen, [RuntimeError])  # the sink failure is reported, not swallowed
        stats = gw.stats()
        self.assertEqual((stats.in_flight, stats.abandoned_in_flight), (0, 0))
        self.assertEqual((stats.timeouts, stats.unclassified_errors), (1, 0))
        self.assertEqual(gw.generate(PROFILE, CALL, deadline_in(2.0)).text, "recovered")


class PeakAccuracyTests(unittest.TestCase):
    def test_reserved_call_whose_start_fails_is_not_a_running_worker(self):
        provider = GatedProvider()
        self.addCleanup(provider.release_all)
        gw = gateway(provider, max_calls=3)
        hold = threading.Event()
        entered = threading.Event()
        real_start = threading.Thread.start
        starts = {"n": 0}

        def start_hook(thread):
            if not thread.name.startswith("sanour-model-"):
                return real_start(thread)  # the test's own caller threads
            starts["n"] += 1
            if starts["n"] == 2:  # request B: reserved, then its start fails
                entered.set()
                hold.wait(5.0)
                raise RuntimeError("B start failed")
            return real_start(thread)

        results = {}

        def run(name):
            try:
                results[name] = gw.generate(PROFILE, CALL, deadline_in(5.0)).text
            except SanourError as exc:
                results[name] = exc.code

        with mock.patch.object(threading.Thread, "start", start_hook):
            a = threading.Thread(target=run, args=("A",))
            a.start()
            self.assertTrue(wait_until(lambda: provider.calls == 1))
            b = threading.Thread(target=run, args=("B",))
            b.start()
            self.assertTrue(entered.wait(5.0))
            self.assertEqual(gw.stats().in_flight, 2)  # A running, B reserved
            c = threading.Thread(target=run, args=("C",))
            c.start()
            self.assertTrue(wait_until(lambda: provider.calls == 2))
            mid = gw.stats()
            # in_flight counts reserved B too (3). Running workers are A and C only (2).
            self.assertEqual((mid.in_flight, mid.peak_in_flight), (3, 2))
            hold.set()
            b.join(5.0)
            self.assertEqual(results["B"], ErrorCode.PROVIDER_FAILED)
            provider.release_all()
            a.join(5.0)
            c.join(5.0)
        self.assertTrue(wait_until(lambda: gw.in_flight == 0))
        final = gw.stats()
        self.assertEqual(final.peak_in_flight, 2)
        self.assertEqual((final.successful_calls, final.provider_failures), (2, 1))
        self.assertEqual((final.in_flight, final.abandoned_in_flight), (0, 0))
