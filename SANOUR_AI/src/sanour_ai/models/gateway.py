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

Slot ownership on launch
- A call takes its slot and counter before the thread is built. Ownership is decided under the
  gateway lock by a per-call flag: the worker claims the call when it begins running, and a
  caller that fails before the claim cancels it. Exactly one side releases the slot and
  decrements in_flight. A worker that never claimed the call never runs the provider.
- If a start failure happens after the worker has claimed the call, the worker keeps ownership.

Process-control exceptions (BaseException)
- A BaseException raised by a provider is captured in the worker and re-raised unchanged in the
  caller thread. It is never classified as an invalid response.
- It is counted in unclassified_errors, and the exception still propagates to the caller.
- If the caller already timed out, the exception cannot reach anyone. It is logged as a warning
  containing only the exception type name, and it is not counted in any outcome category.

Metrics
- Each generate() call records exactly one terminal outcome, decided by the caller.
  A worker that finishes after its caller timed out is never a successful call.
- peak_in_flight is the highest number of workers running at the same time. A worker counts
  from the moment it claims its call until it returns, abandoned workers included. A call that is
  reserved but never starts, or is cancelled before its worker claims it, never counts.
  This is tracked separately from in_flight, which keeps its legacy meaning.
- If a log sink fails while recording an abandoned BaseException, capacity and the caller are
  still released. The sink's error then reaches threading.excepthook. The BaseException's type
  name is lost in that case.
- All counters are guarded by the gateway lock. The lock is never held during a provider call
  or a wait. stats() returns an immutable snapshot.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field

from sanour_ai.errors import ErrorCode, SanourError
from sanour_ai.models.base import ModelCall, ModelProvider, ModelResult, ProviderError
from sanour_ai.models.capabilities import ModelProfile

MAX_RESULT_CHARS = 100_000
_log = logging.getLogger("sanour_ai.gateway")


class GatewayStats(BaseModel):
    """Immutable point-in-time snapshot of gateway counters. Every field is a non-negative int."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    successful_calls: int = Field(ge=0)
    provider_failures: int = Field(ge=0)
    timeouts: int = Field(ge=0)
    overload_rejections: int = Field(ge=0)
    invalid_responses: int = Field(ge=0)
    unclassified_errors: int = Field(ge=0)
    in_flight: int = Field(ge=0)
    abandoned_in_flight: int = Field(ge=0)
    peak_in_flight: int = Field(ge=0)


class _CallState:
    """Per-call ownership and status flags. Guarded by ModelGateway._lock."""

    __slots__ = ("started", "cancelled", "done", "abandoned")

    def __init__(self) -> None:
        self.started = False  # worker has claimed the call and will run the provider
        self.cancelled = False  # caller released the call before any worker claimed it
        self.done = False  # worker has finished and released its counters
        self.abandoned = False  # caller gave up while the worker was still running


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
        # Legacy counter: wait-expiry timeouts only. Keeps the original `timeouts` meaning.
        self._timeouts = 0
        self._in_flight = 0
        self._running = 0  # workers that claimed a call and have not returned
        # Metrics counters (all guarded by _lock).
        self._successful = 0
        self._provider_failures = 0
        self._timeout_outcomes = 0
        self._overloads = 0
        self._invalid_responses = 0
        self._unclassified = 0
        self._abandoned = 0
        self._peak = 0

    @property
    def in_flight(self) -> int:
        """Live worker threads, including abandoned ones that have not returned yet."""
        with self._lock:
            return self._in_flight

    @property
    def timeouts(self) -> int:
        """Wait-expiry timeouts only. Expired-before-start calls are not included here."""
        with self._lock:
            return self._timeouts

    def stats(self) -> GatewayStats:
        """Read-only snapshot. Does not change any counter."""
        with self._lock:
            successful = self._successful
            provider_failures = self._provider_failures
            timeouts = self._timeout_outcomes
            overloads = self._overloads
            invalid = self._invalid_responses
            unclassified = self._unclassified
            in_flight = self._in_flight
            abandoned = self._abandoned
            peak = self._peak
        return GatewayStats(
            successful_calls=successful,
            provider_failures=provider_failures,
            timeouts=timeouts,
            overload_rejections=overloads,
            invalid_responses=invalid,
            unclassified_errors=unclassified,
            in_flight=in_flight,
            abandoned_in_flight=abandoned,
            peak_in_flight=peak,
        )

    def generate(self, profile: ModelProfile, call: ModelCall, deadline: float) -> ModelResult:
        try:
            result = self._generate(profile, call, deadline)
        except SanourError as exc:
            self._record_failure(exc.code)
            raise
        except BaseException:
            # Any other exception escaping generate(), including BaseException, is unclassified.
            with self._lock:
                self._unclassified += 1
            raise
        with self._lock:
            self._successful += 1
        return result

    def _record_failure(self, code: ErrorCode) -> None:
        with self._lock:
            if code is ErrorCode.PROVIDER_TIMEOUT:
                self._timeout_outcomes += 1
            elif code is ErrorCode.PROVIDER_FAILED:
                self._provider_failures += 1
            elif code is ErrorCode.PROVIDER_OVERLOADED:
                self._overloads += 1
            elif code is ErrorCode.PROVIDER_INVALID_RESPONSE:
                self._invalid_responses += 1
            else:
                self._unclassified += 1

    def _cancel_unclaimed(self, state: _CallState) -> bool:
        """Release the caller's slot and counter if no worker has claimed the call.

        Returns True if a worker already claimed the call, in which case the worker owns cleanup.
        """
        with self._lock:
            if state.started:
                return True
            state.cancelled = True
            self._in_flight -= 1
        self._slots.release()
        return False

    def _mark_abandoned(self, state: _CallState) -> None:
        with self._lock:
            if not state.done and not state.abandoned:
                state.abandoned = True
                self._abandoned += 1

    def _generate(self, profile: ModelProfile, call: ModelCall, deadline: float) -> ModelResult:
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
        state = _CallState()

        def _worker() -> None:
            with self._lock:
                if state.cancelled:
                    # The caller already released the slot and counter. Do nothing.
                    return
                state.started = True
                self._running += 1
                self._peak = max(self._peak, self._running)
            try:
                try:
                    outcome["result"] = provider.generate(call)
                except Exception as exc:  # provider code is untrusted; capture everything
                    outcome["error"] = exc
                except BaseException as exc:  # process-control exceptions: re-raised by caller
                    outcome["fatal"] = exc
                finally:
                    with self._lock:
                        state.done = True
                        self._in_flight -= 1
                        self._running -= 1
                        log_fatal = False
                        if state.abandoned:
                            self._abandoned -= 1
                            log_fatal = "fatal" in outcome
                    if log_fatal:
                        # A failing log sink may raise here. The outer finally still runs.
                        _log.warning(
                            "abandoned provider call raised %s", type(outcome["fatal"]).__name__
                        )
            finally:
                # Unconditional: capacity is returned and the caller is released
                # even if the logging call above raised.
                self._slots.release()
                finished.set()

        try:
            with self._lock:
                self._in_flight += 1
            thread = threading.Thread(
                target=_worker, name=f"sanour-model-{profile.alias}", daemon=True
            )
            thread.start()
        except BaseException as exc:
            claimed = self._cancel_unclaimed(state)
            if claimed:
                # A worker is already running and owns cleanup. The caller is leaving early.
                self._mark_abandoned(state)
            if isinstance(exc, Exception):
                raise SanourError(ErrorCode.PROVIDER_FAILED, "model provider failed") from None
            raise

        try:
            finished_in_time = finished.wait(remaining)
        except BaseException:
            self._mark_abandoned(state)
            raise
        if not finished_in_time:
            with self._lock:
                self._timeouts += 1
            self._mark_abandoned(state)
            raise SanourError(ErrorCode.PROVIDER_TIMEOUT, "model call timed out", retryable=True)

        fatal = outcome.get("fatal")
        if isinstance(fatal, BaseException):
            raise fatal

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
