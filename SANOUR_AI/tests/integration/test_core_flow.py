import time
import unittest

from sanour_ai.config.settings import RuntimeSettings
from sanour_ai.core.sanour_core import SanourCore, build_mock_core
from sanour_ai.errors import ErrorCode
from sanour_ai.models.capabilities import ModelCapabilities, ModelProfile
from sanour_ai.models.gateway import ModelGateway
from sanour_ai.models.router import DeterministicRouter
from sanour_ai.observability.audit import AuditLogger
from sanour_ai.providers.mock import TEST_FAKE_SECRET, MockBehavior, MockProvider
from tests.support import TOLERANCE_SECONDS, GatedProvider, wait_until


def profile(alias, provider, priority=100, runs_locally=True):
    return ModelProfile(
        alias=alias,
        provider=provider,
        model_id=f"{alias}-id",
        capabilities=ModelCapabilities(
            text_generation=True, max_context_tokens=8192, max_output_tokens=4096
        ),
        runs_locally=runs_locally,
        priority=priority,
    )


def make_core(providers, profiles, max_concurrent=4):
    return SanourCore(
        router=DeterministicRouter(profiles),
        gateway=ModelGateway(providers, max_concurrent_calls=max_concurrent),
        audit=AuditLogger(),
    )


class CoreFlowTests(unittest.TestCase):
    def test_success_end_to_end(self):
        resp = build_mock_core().handle({"prompt": "Explain recursion in one line"})
        self.assertEqual(resp.status, "success")
        self.assertIn("Explain recursion", resp.output)
        self.assertEqual(len(resp.request_id), 32)
        self.assertEqual(resp.model_alias, "mock_default")
        self.assertGreaterEqual(resp.latency_ms, 0)
        self.assertIsNone(resp.error)

    def test_each_request_gets_new_identifier(self):
        core = build_mock_core()
        self.assertNotEqual(
            core.handle({"prompt": "same"}).request_id,
            core.handle({"prompt": "same"}).request_id,
        )

    def test_invalid_input_never_reaches_provider(self):
        provider = MockProvider(name="mock")
        core = make_core({"mock": provider}, [profile("test_model", "mock")])
        resp = core.handle({"prompt": ""})
        self.assertEqual(resp.error.code, ErrorCode.INVALID_REQUEST)
        self.assertEqual(provider.call_count, 0)

    def test_fallback_flag_is_rejected_as_invalid_input(self):
        provider = MockProvider(name="mock")
        core = make_core({"mock": provider}, [profile("test_model", "mock")])
        resp = core.handle({"prompt": "hi", "allow_fallback": True})
        self.assertEqual(resp.error.code, ErrorCode.INVALID_REQUEST)
        self.assertEqual(provider.call_count, 0)

    def test_provider_failure_is_structured_and_safe(self):
        provider = MockProvider(name="mock", behavior=MockBehavior.FAIL_WITH_SECRET)
        core = make_core({"mock": provider}, [profile("test_model", "mock")])
        resp = core.handle({"prompt": "hi"})
        self.assertEqual(resp.error.code, ErrorCode.PROVIDER_FAILED)
        self.assertFalse(resp.error.retryable)
        self.assertNotIn(TEST_FAKE_SECRET, resp.model_dump_json())
        self.assertNotIn("Traceback", resp.model_dump_json())

    def test_total_deadline_holds_within_tolerance(self):
        provider = MockProvider(name="mock", behavior=MockBehavior.SLOW, delay_seconds=2.0)
        core = make_core({"mock": provider}, [profile("test_model", "mock")])
        start = time.monotonic()
        resp = core.handle({"prompt": "hi", "timeout_seconds": 0.3})
        elapsed = time.monotonic() - start
        self.assertEqual(resp.error.code, ErrorCode.PROVIDER_TIMEOUT)
        self.assertTrue(resp.error.retryable)
        self.assertLess(elapsed, 0.3 + TOLERANCE_SECONDS)
        self.assertGreaterEqual(resp.latency_ms, 250)

    def test_single_attempt_on_retryable_failure(self):
        primary = MockProvider(name="primary_mock", behavior=MockBehavior.FAIL_RETRYABLE)
        backup = MockProvider(name="backup_mock")
        core = make_core(
            {"primary_mock": primary, "backup_mock": backup},
            [
                profile("primary_model", "primary_mock", 1),
                profile("backup_model", "backup_mock", 2),
            ],
        )
        resp = core.handle({"prompt": "hi"})
        self.assertEqual(resp.error.code, ErrorCode.PROVIDER_FAILED)
        self.assertTrue(resp.error.retryable)
        self.assertEqual(primary.call_count, 1)
        self.assertEqual(backup.call_count, 0)

    def test_no_eligible_model_for_offline_request(self):
        provider = MockProvider(name="mock")
        core = make_core({"mock": provider}, [profile("remote_model", "mock", runs_locally=False)])
        resp = core.handle({"prompt": "hi", "offline_only": True})
        self.assertEqual(resp.error.code, ErrorCode.NO_ELIGIBLE_MODEL)
        self.assertEqual(provider.call_count, 0)

    def test_overload_returns_structured_error_then_recovers(self):
        provider = GatedProvider()
        self.addCleanup(provider.release_all)
        core = make_core({"mock": provider}, [profile("test_model", "mock")], max_concurrent=1)

        first = core.handle({"prompt": "hi", "timeout_seconds": 0.05})
        self.assertEqual(first.error.code, ErrorCode.PROVIDER_TIMEOUT)
        self.assertTrue(wait_until(lambda: provider.calls == 1))

        second = core.handle({"prompt": "hi"})
        self.assertEqual(second.error.code, ErrorCode.PROVIDER_OVERLOADED)
        self.assertEqual(provider.calls, 1)

        provider.release_all()
        self.assertTrue(wait_until(lambda: core._gateway.in_flight == 0))
        third = core.handle({"prompt": "hi"})
        self.assertEqual(third.status, "success")

    def test_internal_error_is_generic(self):
        class BrokenRouter:
            def select(self, request):
                raise RuntimeError("internal detail sk-INTERNALKEY12345678")

        core = SanourCore(
            router=BrokenRouter(),
            gateway=ModelGateway({}, max_concurrent_calls=1),
            audit=AuditLogger(),
        )
        resp = core.handle({"prompt": "hi"})
        self.assertEqual(resp.error.code, ErrorCode.INTERNAL_ERROR)
        payload = resp.model_dump_json()
        self.assertNotIn("internal detail", payload)
        self.assertNotIn("sk-INTERNAL", payload)

    def test_secrets_and_prompts_never_reach_logs(self):
        provider = MockProvider(name="mock", behavior=MockBehavior.FAIL_WITH_SECRET)
        core = make_core({"mock": provider}, [profile("test_model", "mock")])
        with self.assertLogs("sanour_ai", level="DEBUG") as logs:
            core.handle({"prompt": "PRIVATE PROMPT TEXT"})
        joined = "\n".join(logs.output)
        self.assertNotIn(TEST_FAKE_SECRET, joined)
        self.assertNotIn("PRIVATE PROMPT TEXT", joined)
        self.assertIn("request.completed", joined)

    def test_settings_drive_worker_cap(self):
        core = build_mock_core(RuntimeSettings(max_concurrent_provider_calls=3))
        self.assertEqual(core._gateway.max_concurrent_calls, 3)
