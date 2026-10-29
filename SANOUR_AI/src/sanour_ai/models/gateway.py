"""Model gateway: one deadline-bounded provider call per request, with bounded worker threads.

Timeout and worker policy
- The caller passes an absolute deadline (a time.monotonic() value). The gateway waits only for
  the time that remains. An expired deadline fails before any provider call is made.
- Provider calls run in daemon threads. Python cannot forcibly stop a thread. A call that
  outlives its deadline keeps running until the provider returns, and its result is discarded.
  Nothing here claims that an abandoned thread was stopped.
- A semaphore caps live worker threads, including abandoned ones, at max_concurrent_calls.
  When the cap is reached, a new call fails at once with PROVIDER_OVERLOADED and starts no thread.
- A slot is released only when its worker returns. A provider call that never returns keeps one
  slot for the life of the process. This is a known limit; recovering it requires a restart.
- There is no cancellation and no retry.
- Caller-side latency: the caller returns about when the deadline passes. Scheduling overhead
  is typically well under the 0.25 s tolerance the tests use.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping

from sanour_ai.errors import ErrorCode, SanourError
from sanour_ai.models.base import ModelCall, ModelProvider, ModelResult, ProviderError
from sanour_ai.models.capabilities import ModelProfile

MAX_RESULT_CHARS = 100_000


class ModelGateway:
    def __init__(
        self, providers: Mapping[str, ModelProvider], *, max_concurrent_calls: int
    ) -> None:
        if max_concurrent_calls < 1:
            raise ValueError("max_concurrent_calls must be at least 1")
        self._providers = dict(providers)
        self.max_concurrent_calls = max_concurrent_calls
        self._slots = threading.BoundedSemaphore(max_concurrent_calls)
        self._lock = threading.Lock()
        self._in_flight = 0
        self._timeouts = 0

    @property
    def in_flight(self) -> int:
        """Live worker threads, including abandoned ones that have not returned yet."""
        with self._lock:
            return self._in_flight

    @property
    def timeouts(self) -> int:
        with self._lock:
            return self._timeouts

    def generate(self, profile: ModelProfile, call: ModelCall, deadline: float) -> ModelResult:
        provider = self._providers.get(profile.provider)
        if provider is None:
            raise SanourError(ErrorCode.PROVIDER_FAILED, "model provider is not registered")

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SanourError(ErrorCode.PROVIDER_TIMEOUT, "model call timed out", retryable=True)

        if not self._slots.acquire(blocking=False):
            raise SanourError(
                ErrorCode.PROVIDER_OVERLOADED,
                "model capacity is full, try again shortly",
                retryable=True,
            )

        outcome: dict[str, object] = {}
        finished = threading.Event()

        def _worker() -> None:
            try:
                outcome["result"] = provider.generate(call)
            except Exception as exc:  # provider code is untrusted; capture everything
                outcome["error"] = exc
            finally:
                with self._lock:
                    self._in_flight -= 1
                self._slots.release()
                finished.set()

        with self._lock:
            self._in_flight += 1
        thread = threading.Thread(
            target=_worker, name=f"sanour-model-{profile.alias}", daemon=True
        )
        try:
            thread.start()
        except RuntimeError:
            with self._lock:
                self._in_flight -= 1
            self._slots.release()
            raise SanourError(ErrorCode.PROVIDER_FAILED, "model provider failed") from None

        if not finished.wait(remaining):
            with self._lock:
                self._timeouts += 1
            raise SanourError(ErrorCode.PROVIDER_TIMEOUT, "model call timed out", retryable=True)

        if "error" in outcome:
            exc = outcome["error"]
            retryable = isinstance(exc, ProviderError) and exc.retryable
            raise SanourError(
                ErrorCode.PROVIDER_FAILED, "model provider failed", retryable=retryable
            )

        result = outcome.get("result")
        if (
            not isinstance(result, ModelResult)
            or not result.text.strip()
            or len(result.text) > MAX_RESULT_CHARS
        ):
            raise SanourError(
                ErrorCode.PROVIDER_INVALID_RESPONSE, "model returned an invalid response"
            )
        return result
