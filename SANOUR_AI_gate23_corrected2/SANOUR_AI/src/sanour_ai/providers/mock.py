"""Deterministic mock provider. No network, no credentials, no randomness."""

from __future__ import annotations

import time
from enum import StrEnum

from sanour_ai.models.base import ModelCall, ModelResult, ProviderError

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
