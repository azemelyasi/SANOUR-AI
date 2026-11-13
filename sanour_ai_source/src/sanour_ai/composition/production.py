"""Production composition root (Stage 10.2). Opt-in only.

- Nothing is enabled by default. SANOUR_PROVIDER must be set to exactly "openai".
- No mock, no fallback, no second provider, no automatic model choice.
- Model id and token limits come only from the environment. No guessed defaults.
- Building the core sends no network request. The first request happens only in handle().
- Every configuration error names the setting, never its value.
"""

from __future__ import annotations

import importlib.util
import os
import re
from collections.abc import Mapping

from sanour_ai.config.settings import ConfigError, load_settings
from sanour_ai.core.sanour_core import SanourCore
from sanour_ai.models.base import ModelProvider
from sanour_ai.models.capabilities import ModelCapabilities, ModelProfile
from sanour_ai.models.gateway import ModelGateway
from sanour_ai.models.router import DeterministicRouter
from sanour_ai.observability.audit import AuditLogger
from sanour_ai.providers.openai_provider import (
    OPENAI_PROVIDER_NAME,
    OpenAIProvider,
    check_environment,
    load_api_key,
)

ENV_PROVIDER = "SANOUR_PROVIDER"
ENV_OPENAI_MODEL_ID = "SANOUR_OPENAI_MODEL_ID"
ENV_OPENAI_MAX_CONTEXT_TOKENS = "SANOUR_OPENAI_MAX_CONTEXT_TOKENS"
ENV_OPENAI_MAX_OUTPUT_TOKENS = "SANOUR_OPENAI_MAX_OUTPUT_TOKENS"

OPENAI_ALIAS = "openai_primary"

# Positive whole numbers only: no sign, no decimals, no leading zero, no spaces.
_POSITIVE_INT = re.compile(r"^[1-9][0-9]{0,11}$")
# Model ids are opaque strings from the operator. Only the shape is checked, never the value.
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,127}$")


def _required(env: Mapping[str, str], name: str) -> str:
    raw = env.get(name)
    if raw is None or not raw.strip():
        raise ConfigError(f"{name} must be set")
    return raw.strip()


def _positive_int(env: Mapping[str, str], name: str) -> int:
    raw = _required(env, name)
    if not _POSITIVE_INT.match(raw):
        raise ConfigError(f"{name} must be a positive whole number")
    return int(raw)


def _model_id(env: Mapping[str, str]) -> str:
    raw = _required(env, ENV_OPENAI_MODEL_ID)
    if not _MODEL_ID.match(raw):
        raise ConfigError(f"{ENV_OPENAI_MODEL_ID} has an invalid format")
    return raw


def _sdk_present() -> bool:
    return (
        importlib.util.find_spec("openai") is not None
        and importlib.util.find_spec("httpx2") is not None
    )


def _assemble(
    profile: ModelProfile,
    providers: Mapping[str, ModelProvider],
    max_concurrent_calls: int,
) -> SanourCore:
    """Wire the router, gateway, and core around one profile. Shared by production and tests."""
    return SanourCore(
        router=DeterministicRouter([profile]),
        gateway=ModelGateway(providers, max_concurrent_calls=max_concurrent_calls),
        audit=AuditLogger(),
    )


def build_production_core(environ: Mapping[str, str] | None = None) -> SanourCore:
    """Build the provider-backed core, or raise ConfigError. Never falls back to anything else."""
    env: Mapping[str, str] = os.environ if environ is None else environ

    # Exact match only. No stripping, no case folding: the switch must say exactly "openai".
    if env.get(ENV_PROVIDER) != OPENAI_PROVIDER_NAME:
        raise ConfigError(f"{ENV_PROVIDER} must be set to '{OPENAI_PROVIDER_NAME}' to enable a provider")

    settings = load_settings(env)
    check_environment(env)  # OPENAI_BASE_URL, OPENAI_CUSTOM_HEADERS, OPENAI_ORG_ID, OPENAI_PROJECT_ID

    model_id = _model_id(env)
    max_context = _positive_int(env, ENV_OPENAI_MAX_CONTEXT_TOKENS)
    max_output = _positive_int(env, ENV_OPENAI_MAX_OUTPUT_TOKENS)
    if max_output > max_context:
        raise ConfigError(
            f"{ENV_OPENAI_MAX_OUTPUT_TOKENS} must not be greater than {ENV_OPENAI_MAX_CONTEXT_TOKENS}"
        )

    api_key = load_api_key(env)  # names the variable on failure, never its value

    if not _sdk_present():
        raise ConfigError("the OpenAI SDK is not installed; the openai extra is required")

    profile = ModelProfile(
        alias=OPENAI_ALIAS,
        provider=OPENAI_PROVIDER_NAME,
        model_id=model_id,
        capabilities=ModelCapabilities(
            text_generation=True,
            max_context_tokens=max_context,
            max_output_tokens=max_output,
        ),
        runs_locally=False,
    )
    provider = OpenAIProvider(api_key)
    return _assemble(profile, {OPENAI_PROVIDER_NAME: provider}, settings.max_concurrent_provider_calls)
