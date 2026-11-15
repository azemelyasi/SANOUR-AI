"""Provider protocol. Every adapter, including the future SANOUR model, implements this."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ModelCall:
    """One provider attempt.

    deadline: absolute time.monotonic() value for the whole request, set by SanourCore.handle().
    The core is the only place it is computed. No layer may extend or reset it. It is None only
    for callers that predate this field; the request path always sets it.

    Contract for future adapters (documentation only; no adapter exists yet):
    - Compute the remaining budget as deadline - time.monotonic() at the start of every I/O step.
      If it is <= 0, fail with ProviderError(retryable=True) before any network I/O.
    - Every internal timeout (connect, read, write, pool) must be min(configured, remaining).
      Read timeouts are per read, so also check the remaining budget between reads.
    - Stop I/O when the deadline passes where the transport allows it. The gateway stays the
      authority: a late result is discarded, and the worker keeps its slot until it returns.
    - No internal retries. Cancellation is not part of this contract.
    """

    model_id: str
    prompt: str
    max_output_tokens: int
    deadline: float | None = None


@dataclass(frozen=True)
class ModelResult:
    text: str
    model_id: str


class ProviderError(Exception):
    """Raised by provider adapters. `detail` is for internal use only and never reaches callers."""

    def __init__(self, detail: str, *, retryable: bool = False) -> None:
        super().__init__(detail)
        self.retryable = retryable


@runtime_checkable
class ModelProvider(Protocol):
    name: str

    def generate(self, call: ModelCall) -> ModelResult: ...
