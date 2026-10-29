"""Minimal typed runtime settings. Only operational limits live here. No secrets.

Phase 1 needs one configurable value: the cap on live provider worker threads.
Invalid values fail at startup. Error messages name the setting but never echo its value.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError

ENV_MAX_CONCURRENT_PROVIDER_CALLS = "SANOUR_MAX_CONCURRENT_PROVIDER_CALLS"


class ConfigError(ValueError):
    """Raised at startup for invalid configuration."""


class RuntimeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_concurrent_provider_calls: int = Field(default=4, ge=1, le=16)


def load_settings(environ: Mapping[str, str] | None = None) -> RuntimeSettings:
    env = os.environ if environ is None else environ
    raw = env.get(ENV_MAX_CONCURRENT_PROVIDER_CALLS)
    if raw is None:
        return RuntimeSettings()
    try:
        value = int(raw.strip())
    except ValueError:
        raise ConfigError(f"{ENV_MAX_CONCURRENT_PROVIDER_CALLS} must be an integer") from None
    try:
        return RuntimeSettings(max_concurrent_provider_calls=value)
    except ValidationError:
        raise ConfigError(
            f"{ENV_MAX_CONCURRENT_PROVIDER_CALLS} must be between 1 and 16"
        ) from None
