"""Request schemas. Clients send SanourRequestInput; the core adds the request id."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_PROMPT_CHARS = 8000
MAX_OUTPUT_TOKENS = 4096
MAX_TIMEOUT_SECONDS = 60.0
MAX_METADATA_ITEMS = 20
MAX_METADATA_VALUE_CHARS = 200

_METADATA_KEY = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
_ALLOWED_CONTROL = frozenset({"\n", "\t", "\r"})


def _reject_control_chars(value: str, field: str) -> str:
    for ch in value:
        if ord(ch) < 32 and ch not in _ALLOWED_CONTROL:
            raise ValueError(f"{field} contains control characters")
    return value


class SanourRequestInput(BaseModel):
    """What a client may send. Clients cannot choose request identifiers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS)
    task_type: Literal["general_text"] = "general_text"
    max_output_tokens: int = Field(default=512, ge=1, le=MAX_OUTPUT_TOKENS)
    timeout_seconds: float = Field(default=10.0, gt=0, le=MAX_TIMEOUT_SECONDS)
    offline_only: bool = False
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("prompt")
    @classmethod
    def _prompt_is_usable(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt must not be blank")
        return _reject_control_chars(value, "prompt")

    @field_validator("metadata")
    @classmethod
    def _metadata_is_bounded(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > MAX_METADATA_ITEMS:
            raise ValueError("too many metadata items")
        for key, val in value.items():
            if not _METADATA_KEY.match(key):
                raise ValueError("invalid metadata key")
            if len(val) > MAX_METADATA_VALUE_CHARS:
                raise ValueError("metadata value too long")
            _reject_control_chars(val, "metadata value")
        return value


class SanourRequest(SanourRequestInput):
    """A validated request with a server-assigned identifier."""

    request_id: str = Field(min_length=32, max_length=32)
