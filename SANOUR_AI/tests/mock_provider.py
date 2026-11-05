"""TEST ONLY. Deterministic mock provider and test composition root. Not part of src/."""

from __future__ import annotations

import time
from enum import StrEnum

from sanour_ai.config.settings import RuntimeSettings, load_settings
from sanour_ai.core.sanour_core import SanourCore
from sanour_ai.models.capabilities import ModelCapabilities, ModelProfile
from sanour_ai.models.gateway import ModelGateway
from sanour_ai.models.router import DeterministicRouter
from sanour_ai.models.base import ModelCall, ModelResult, ProviderError
from sanour_ai.observability.audit import AuditLogger

# Fake credential used only to test redaction. It is not a real key.
TEST_FAKE_SECRET = "sk-FAKETESTKEY0123456789ABCDEF"


class MockBehavior(StrEnum):
    ECHO = "echo"
    FAIL_RETRYABLE = "fail_retryable"
    FAIL_WITH_SECRET = "fail_with_secret"
    SLOW = "slow"
    EMPTY = "empty"


class MockProvider:
    def __init__(
        self,
        name: str = "mock",
        behavior: MockBehavior = MockBehavior.ECHO,
        delay_seconds: float = 0.0,
    ) -> None:
        self.name = name
        self._behavior = behavior
        self._delay = delay_seconds
        self.call_count = 0

    def generate(self, call: ModelCall) -> ModelResult:
        self.call_count += 1
        if self._behavior is MockBehavior.FAIL_RETRYABLE:
            raise ProviderError("mock upstream unavailable", retryable=True)
        if self._behavior is MockBehavior.FAIL_WITH_SECRET:
            raise ProviderError(f"mock auth failed for key {TEST_FAKE_SECRET}")
        if self._behavior is MockBehavior.SLOW:
            time.sleep(self._delay)
        if self._behavior is MockBehavior.EMPTY:
            return ModelResult(text="   ", model_id=call.model_id)
        return ModelResult(text=f"[mock:{call.model_id}] {call.prompt}", model_id=call.model_id)


def build_mock_core(
    settings: RuntimeSettings | None = None,
    behavior: MockBehavior = MockBehavior.ECHO,
    delay_seconds: float = 0.0,
) -> SanourCore:
    """Composition root for tests and local runs. Uses only the mock provider.

    Explicit settings win. When none are given, settings come from the validated environment
    (load_settings), so an invalid environment value raises ConfigError here.
    """
    if settings is None:
        settings = load_settings()
    provider = MockProvider(behavior=behavior, delay_seconds=delay_seconds)
    profile = ModelProfile(
        alias="mock_default",
        provider="mock",
        model_id="mock-echo-1",
        capabilities=ModelCapabilities(
            text_generation=True, max_context_tokens=8192, max_output_tokens=4096
        ),
        runs_locally=True,
    )
    return SanourCore(
        router=DeterministicRouter([profile]),
        gateway=ModelGateway(
            {"mock": provider},
            max_concurrent_calls=settings.max_concurrent_provider_calls,
        ),
        audit=AuditLogger(),
    )
