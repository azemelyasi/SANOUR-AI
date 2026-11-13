import time
import unittest

from sanour_ai.core.sanour_core import SanourCore
from sanour_ai.models.base import ModelCall, ModelResult
from sanour_ai.models.capabilities import ModelCapabilities, ModelProfile
from sanour_ai.models.router import DeterministicRouter
from sanour_ai.observability.audit import AuditLogger

PROFILE = ModelProfile(
    alias="recording_default",
    provider="recording",
    model_id="recording-1",
    capabilities=ModelCapabilities(
        text_generation=True, max_context_tokens=8192, max_output_tokens=4096
    ),
    runs_locally=True,
)


class _RecordingGateway:
    """Stands in for ModelGateway: records what the core hands over. Returns a valid result."""

    def __init__(self):
        self.calls = []

    def generate(self, profile, call, deadline):
        self.calls.append((call, deadline))
        return ModelResult(text="ok", model_id=call.model_id)


class ModelCallContractTests(unittest.TestCase):
    def test_three_argument_constructor_still_works(self):
        call = ModelCall(model_id="m", prompt="hi", max_output_tokens=16)
        self.assertIsNone(call.deadline)
        self.assertEqual((call.model_id, call.prompt, call.max_output_tokens), ("m", "hi", 16))

    def test_constructor_accepts_deadline(self):
        when = time.monotonic() + 5.0
        call = ModelCall(model_id="m", prompt="hi", max_output_tokens=16, deadline=when)
        self.assertEqual(call.deadline, when)

    def test_model_call_is_still_frozen(self):
        call = ModelCall(model_id="m", prompt="hi", max_output_tokens=16)
        with self.assertRaises(Exception):
            call.deadline = 1.0

    def test_core_passes_the_gateway_deadline_into_model_call(self):
        gateway = _RecordingGateway()
        core = SanourCore(
            router=DeterministicRouter([PROFILE]), gateway=gateway, audit=AuditLogger()
        )
        before = time.monotonic()
        resp = core.handle({"prompt": "hello", "timeout_seconds": 3.0})
        after = time.monotonic()
        self.assertEqual(resp.status, "success")
        self.assertEqual(len(gateway.calls), 1)
        call, gateway_deadline = gateway.calls[0]
        # The main path never leaves the deadline unset.
        self.assertIsNotNone(call.deadline)
        # Same value in both layers: no second clock, no re-computation.
        self.assertEqual(call.deadline, gateway_deadline)
        # It is the request start plus the requested budget, measured with monotonic time.
        self.assertGreaterEqual(call.deadline - 3.0, before)
        self.assertLessEqual(call.deadline - 3.0, after)

    def test_deadline_is_not_extended_by_validation_or_routing(self):
        gateway = _RecordingGateway()
        core = SanourCore(
            router=DeterministicRouter([PROFILE]), gateway=gateway, audit=AuditLogger()
        )
        before = time.monotonic()
        core.handle({"prompt": "hello", "timeout_seconds": 2.0})
        call, gateway_deadline = gateway.calls[0]
        # Still anchored at the start of handle(), not at the time the gateway was reached.
        self.assertLessEqual(call.deadline, before + 2.0 + 0.5)
        self.assertEqual(call.deadline, gateway_deadline)


if __name__ == "__main__":
    unittest.main()
