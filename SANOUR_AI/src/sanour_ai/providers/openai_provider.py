"""OpenAI provider adapter (Responses API). Production code: no mocks, no retries, no fallback.

Contract: see models/base.py. The only deadline is ModelCall.deadline. It is never extended,
reset, or recomputed here.

Known limit (Trickle): the four httpx timeouts cap each I/O step, but they do not guarantee an
overall deadline against slow trickled data. Network work can continue after the deadline. The
gateway returns to the caller on time and discards the late result, but the worker thread and its
semaphore slot stay busy until the SDK call returns. No cancellation is claimed.
"""

from __future__ import annotations

import os
import ssl
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import SecretStr

from sanour_ai.config.settings import ConfigError
from sanour_ai.models.base import ModelCall, ModelResult, ProviderError

ENV_OPENAI_API_KEY = "OPENAI_API_KEY"
OPENAI_PROVIDER_NAME = "openai"

# Configured ceilings per I/O phase. Each is capped by the remaining deadline budget.
CONNECT_TIMEOUT_S = 5.0
READ_TIMEOUT_S = 30.0
WRITE_TIMEOUT_S = 10.0
POOL_TIMEOUT_S = 5.0


class RateLimitedError(ProviderError):
    """Raised for HTTP 429. The gateway still reports PROVIDER_FAILED until it maps this code."""

    error_code = "provider_rate_limited"


@dataclass(frozen=True)
class SdkErrorTypes:
    """Exception classes from the SDK used for mapping. Injected in tests, real SDK by default."""

    base: type
    authentication: type
    permission_denied: type
    not_found: type
    rate_limit: type
    internal_server: type
    api_timeout: type
    api_connection: type
    api_status: type


def default_sdk_error_types() -> SdkErrorTypes:
    import openai  # imported lazily so the core never needs the SDK

    return SdkErrorTypes(
        base=openai.OpenAIError,
        authentication=openai.AuthenticationError,
        permission_denied=openai.PermissionDeniedError,
        not_found=openai.NotFoundError,
        rate_limit=openai.RateLimitError,
        internal_server=openai.InternalServerError,
        api_timeout=openai.APITimeoutError,
        api_connection=openai.APIConnectionError,
        api_status=openai.APIStatusError,
    )


# Endpoint is pinned in code. The environment cannot redirect requests or replace headers.
PINNED_BASE_URL = "https://api.openai.com/v1"
BLOCKED_ENV_VARS = ("OPENAI_BASE_URL", "OPENAI_CUSTOM_HEADERS", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID")

# Exact header names the SDK 3.45.0 generates for every request (lowercase). Nothing else is
# accepted. There is no prefix rule, so a caller cannot add headers such as X-Stainless-Probe.
SDK_DEFAULT_HEADER_NAMES = frozenset({
    "accept",
    "content-type",
    "user-agent",
    "x-stainless-lang",
    "x-stainless-package-version",
    "x-stainless-os",
    "x-stainless-arch",
    "x-stainless-runtime",
    "x-stainless-runtime-version",
    "x-stainless-async",
})
# The SDK sends these only while unset (value is its Omit sentinel). A string value is an override.
SDK_OMITTABLE_HEADER_NAMES = frozenset({"openai-organization", "openai-project"})


def check_environment(environ: Mapping[str, str] | None = None) -> None:
    """Fail closed when a blocked variable is present, even if empty. Names only, never values."""
    env = os.environ if environ is None else environ
    present = [name for name in BLOCKED_ENV_VARS if name in env]
    if present:
        raise ConfigError(
            "unsupported environment settings present: "
            + ", ".join(present)
            + " (the adapter pins its endpoint and does not accept these; unset them)"
        )


def load_api_key(environ: Mapping[str, str] | None = None) -> SecretStr:
    """Read the API key from the environment. Errors name the setting, never the value."""
    env = os.environ if environ is None else environ
    raw = env.get(ENV_OPENAI_API_KEY)
    if raw is None or not raw.strip():
        raise ConfigError(f"{ENV_OPENAI_API_KEY} must be set to a non-empty value")
    return SecretStr(raw.strip())


def _trusted_transport_types() -> tuple[type, ...]:
    """Transport classes the adapter accepts. Production accepts only the httpx2 default transport.

    Unknown transports are refused until a reviewer adds them here.
    """
    import httpx2

    return (httpx2.HTTPTransport,)


def _default_timeout(remaining: float) -> Any:
    import httpx2  # the SDK's transport timeout type

    return httpx2.Timeout(
        connect=min(CONNECT_TIMEOUT_S, remaining),
        read=min(READ_TIMEOUT_S, remaining),
        write=min(WRITE_TIMEOUT_S, remaining),
        pool=min(POOL_TIMEOUT_S, remaining),
    )


def _build_http_client() -> Any:
    import httpx2

    # trust_env=False: HTTP(S)_PROXY, ALL_PROXY, NO_PROXY, SSL_CERT_FILE and SSL_CERT_DIR are
    # ignored. The transport itself is validated on every request by validate_client().
    return httpx2.Client(trust_env=False)


def _build_default_client(api_key: SecretStr) -> Any:
    check_environment()  # before the SDK is imported or any client is built
    import openai

    # Endpoint pinned explicitly. max_retries=0: the SDK must never retry on its own.
    return openai.OpenAI(
        api_key=api_key.get_secret_value(),
        base_url=PINNED_BASE_URL,
        max_retries=0,
        http_client=_build_http_client(),
    )


def _header_problem(name: str, value: Any) -> str | None:
    lname = name.lower()
    if lname == "authorization":
        return "authorization header override is set"
    if lname in SDK_OMITTABLE_HEADER_NAMES:
        return "organization or project header is set" if isinstance(value, str) else None
    if lname in SDK_DEFAULT_HEADER_NAMES:
        return None
    return "custom header is set"


def _transport_problems(transport: Any) -> list[str]:
    import httpx2

    if not isinstance(transport, _trusted_transport_types()):
        return ["transport type is not trusted"]
    if isinstance(transport, httpx2.HTTPTransport):
        pool = getattr(transport, "_pool", None)
        if getattr(pool, "_proxy_url", None) is not None:
            return ["transport uses an explicit proxy"]
        ctx = getattr(pool, "_ssl_context", None)
        if ctx is None or ctx.verify_mode != ssl.CERT_REQUIRED or not ctx.check_hostname:
            return ["TLS verification is disabled"]
    return []


def validate_client(client: Any, api_key: SecretStr) -> None:
    """Accept only a pinned, key-matched, header-clean SDK client on a trusted transport.

    Runs at construction and before every request. Fails closed. Error text names the failed
    checks, never the values involved.
    """
    try:
        import openai
    except ImportError:
        raise ConfigError("client rejected: SDK is not available to verify it") from None

    problems: list[str] = []
    if not isinstance(client, openai.OpenAI):
        problems.append("not an SDK client built by this adapter")
    else:
        if getattr(client, "api_key", None) != api_key.get_secret_value():
            problems.append("api key does not match the adapter key")
        if getattr(client, "auth_headers", None) != {"Authorization": "Bearer " + api_key.get_secret_value()}:
            problems.append("authorization does not come from the adapter key")
        if str(getattr(client, "base_url", "")).rstrip("/") != PINNED_BASE_URL:
            problems.append("endpoint is not the pinned endpoint")
        if getattr(client, "organization", None) is not None or getattr(client, "project", None) is not None:
            problems.append("organization or project is set")
        if getattr(client, "max_retries", None) != 0:
            problems.append("retries are not disabled")
        # admin_api_key is populated from OPENAI_ADMIN_KEY but does not change Responses API auth
        # (tested), so it is not rejected. workload_identity is never set from the environment.
        if getattr(client, "workload_identity", None) is not None:
            problems.append("an alternate credential is set")
        if getattr(client, "default_query", {}):
            problems.append("custom query parameters are set")
        # Both the merged default_headers and caller-supplied _custom_headers are checked by name.
        header_problems: list[str] = []
        for source in (getattr(client, "default_headers", {}), getattr(client, "_custom_headers", {})):
            for name, value in source.items():
                problem = _header_problem(str(name), value)
                if problem and problem not in header_problems:
                    header_problems.append(problem)
        problems.extend(header_problems)
        http = getattr(client, "_client", None)
        if http is None:
            problems.append("no HTTP client")
        else:
            if getattr(http, "_trust_env", True) is not False:
                problems.append("environment proxy or TLS settings are enabled")
            if getattr(http, "_mounts", None):
                problems.append("explicit proxy or per-host transport is configured")
            problems.extend(_transport_problems(getattr(http, "_transport", None)))
    if problems:
        raise ConfigError("client rejected: " + "; ".join(problems))


class OpenAIProvider:
    name = OPENAI_PROVIDER_NAME

    def __init__(
        self,
        api_key: SecretStr,
        *,
        client: Any = None,
        errors: SdkErrorTypes | None = None,
        timeout_factory: Callable[[float], Any] | None = None,
    ) -> None:
        check_environment()  # SDK clients read the environment when they are built
        if client is None:
            client = _build_default_client(api_key)
        # Validation is called directly here, not through an overridable method, so a subclass
        # cannot skip it. The same check runs again before every request.
        validate_client(client, api_key)
        self._api_key = api_key
        self._client = client
        self._errors = errors if errors is not None else default_sdk_error_types()
        self._timeout_factory = timeout_factory or _default_timeout

    def generate(self, call: ModelCall) -> ModelResult:
        if call.deadline is None:
            raise ProviderError("request has no deadline", retryable=False)
        remaining = call.deadline - time.monotonic()
        if remaining <= 0:
            # Fails before any network I/O.
            raise ProviderError("deadline expired before provider call", retryable=True)
        try:
            # Re-check the client and its headers and transport. Changes after construction are
            # caught here, before any request is sent.
            validate_client(self._client, self._api_key)
        except ConfigError:
            raise ProviderError("client configuration changed; request refused", retryable=False) from None

        timeout = self._timeout_factory(remaining)
        try:
            response = self._client.responses.create(
                model=call.model_id,
                input=call.prompt,
                max_output_tokens=call.max_output_tokens,
                store=False,
                timeout=timeout,
            )
        except Exception as exc:  # every SDK failure is mapped; raw text never leaves
            raise self._map_error(exc) from None

        # A response that arrives after the deadline is not accepted.
        if time.monotonic() >= call.deadline:
            raise ProviderError("response arrived after deadline", retryable=True)
        return self._to_result(response)

    def _map_error(self, exc: BaseException) -> ProviderError:
        e = self._errors
        # Order matters: APITimeoutError is a subclass of APIConnectionError.
        if isinstance(exc, e.api_timeout):
            return ProviderError("provider call timed out", retryable=True)
        if isinstance(exc, e.api_connection):
            return ProviderError("provider connection failed", retryable=True)
        if isinstance(exc, e.authentication):
            return ProviderError("provider rejected credentials", retryable=False)
        if isinstance(exc, e.permission_denied):
            return ProviderError("provider denied permission", retryable=False)
        if isinstance(exc, e.not_found):
            return ProviderError("provider model or endpoint not found", retryable=False)
        if isinstance(exc, e.rate_limit):
            return RateLimitedError("provider rate limited", retryable=True)
        if isinstance(exc, e.internal_server):
            return ProviderError("provider server error", retryable=True)
        if isinstance(exc, e.api_status) and getattr(exc, "status_code", 0) >= 500:
            return ProviderError("provider server error", retryable=True)
        return ProviderError("provider call failed", retryable=False)

    @staticmethod
    def _to_result(response: Any) -> ModelResult:
        text = getattr(response, "output_text", None)
        model = getattr(response, "model", None)
        # Never rewrite or invent a model id. An empty id can never equal call.model_id, so the
        # gateway rejects it as PROVIDER_INVALID_RESPONSE.
        return ModelResult(
            text=text if isinstance(text, str) else "",
            model_id=model if isinstance(model, str) else "",
        )
