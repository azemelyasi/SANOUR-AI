"""SanourCore: single entry point. One provider attempt per request, under one total deadline."""

from __future__ import annotations

import time
import uuid

from sanour_ai.config.settings import RuntimeSettings, load_settings
from sanour_ai.core.request_manager import accept
from sanour_ai.errors import ErrorCode, SanourError
from sanour_ai.models.base import ModelCall
from sanour_ai.models.capabilities import ModelCapabilities, ModelProfile
from sanour_ai.models.gateway import ModelGateway
from sanour_ai.models.requests import SanourRequest
from sanour_ai.models.responses import SanourResponse
from sanour_ai.models.router import DeterministicRouter
from sanour_ai.observability.audit import AuditLogger
from sanour_ai.providers.mock import MockBehavior, MockProvider

INTERNAL_ERROR_MESSAGE = "internal error while processing request"


def _elapsed_ms(started: float) -> int:
    return int(round((time.monotonic() - started) * 1000))


class SanourCore:
    def __init__(
        self,
        router: DeterministicRouter,
        gateway: ModelGateway,
        audit: AuditLogger | None = None,
    ) -> None:
        self._router = router
        self._gateway = gateway
        self._audit = audit or AuditLogger()

    def handle(self, raw: object) -> SanourResponse:
        started = time.monotonic()
        request_id = uuid.uuid4().hex
        response = self._process(raw, request_id, started)
        self._audit.emit(
            "request.completed",
            request_id=request_id,
            status=response.status,
            error_code=response.error.code.value if response.error else None,
            latency_ms=response.latency_ms,
        )
        return response

    def _process(self, raw: object, request_id: str, started: float) -> SanourResponse:
        try:
            request = accept(raw, request_id)
            # The deadline starts when handle() starts, so validation and routing count against it.
            deadline = started + request.timeout_seconds
            self._audit.emit("request.received", request_id=request_id, task_type=request.task_type)
            return self._run(request, started, deadline)
        except SanourError as exc:
            return SanourResponse.failure(
                request_id=request_id, error=exc, latency_ms=_elapsed_ms(started)
            )
        except Exception as exc:
            self._audit.emit(
                "request.internal_error", request_id=request_id, exc_type=type(exc).__name__
            )
            internal = SanourError(ErrorCode.INTERNAL_ERROR, INTERNAL_ERROR_MESSAGE)
            return SanourResponse.failure(
                request_id=request_id, error=internal, latency_ms=_elapsed_ms(started)
            )

    def _run(self, request: SanourRequest, started: float, deadline: float) -> SanourResponse:
        profile = self._router.select(request)
        call = ModelCall(
            model_id=profile.model_id,
            prompt=request.prompt,
            max_output_tokens=request.max_output_tokens,
        )
        try:
            result = self._gateway.generate(profile, call, deadline)
        except SanourError as exc:
            self._audit.emit(
                "model.failed",
                request_id=request.request_id,
                model_alias=profile.alias,
                model_id=profile.model_id,
                error_code=exc.code.value,
                retryable=exc.retryable,
            )
            raise
        self._audit.emit(
            "model.succeeded",
            request_id=request.request_id,
            model_alias=profile.alias,
            model_id=profile.model_id,
        )
        return SanourResponse.success(
            request_id=request.request_id,
            output=result.text,
            model_alias=profile.alias,
            model_id=profile.model_id,
            latency_ms=_elapsed_ms(started),
        )


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
