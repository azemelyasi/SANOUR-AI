"""Structured success and error envelopes returned to callers."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, model_validator

from sanour_ai.errors import ErrorCode, SanourError


class ErrorInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: ErrorCode
    message: str
    retryable: bool


class SanourResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: str
    status: Literal["success", "error"]
    output: str | None = None
    model_alias: str | None = None
    model_id: str | None = None
    latency_ms: int = 0
    error: ErrorInfo | None = None

    @model_validator(mode="after")
    def _status_matches_payload(self) -> SanourResponse:
        if self.status == "success" and (self.output is None or self.error is not None):
            raise ValueError("success responses need output and no error")
        if self.status == "error" and (self.error is None or self.output is not None):
            raise ValueError("error responses need an error and no output")
        return self

    @classmethod
    def success(
        cls,
        *,
        request_id: str,
        output: str,
        model_alias: str,
        model_id: str,
        latency_ms: int,
    ) -> SanourResponse:
        return cls(
            request_id=request_id,
            status="success",
            output=output,
            model_alias=model_alias,
            model_id=model_id,
            latency_ms=latency_ms,
        )

    @classmethod
    def failure(cls, *, request_id: str, error: SanourError, latency_ms: int) -> SanourResponse:
        return cls(
            request_id=request_id,
            status="error",
            latency_ms=latency_ms,
            error=ErrorInfo(code=error.code, message=error.message, retryable=error.retryable),
        )
