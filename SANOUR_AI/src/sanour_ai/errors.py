"""Structured error codes and the single exception type used across the core.

Messages carried by SanourError are fixed, safe strings. Raw provider text never
goes into a SanourError message.
"""

from enum import StrEnum


class ErrorCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    NO_ELIGIBLE_MODEL = "no_eligible_model"
    PROVIDER_FAILED = "provider_failed"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_OVERLOADED = "provider_overloaded"
    PROVIDER_INVALID_RESPONSE = "provider_invalid_response"
    INTERNAL_ERROR = "internal_error"


class SanourError(Exception):
    def __init__(self, code: ErrorCode, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
