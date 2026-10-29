"""Provider protocol. Every adapter, including the future SANOUR model, implements this."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ModelCall:
    model_id: str
    prompt: str
    max_output_tokens: int


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
