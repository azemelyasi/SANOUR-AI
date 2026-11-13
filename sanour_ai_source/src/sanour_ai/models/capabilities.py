"""Capability and profile metadata. Capabilities are declared, never assumed."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ModelCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text_generation: bool = False
    structured_output: bool = False
    tool_calling: bool = False
    image_input: bool = False
    image_output: bool = False
    video_output: bool = False
    streaming: bool = False
    max_context_tokens: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)


class ModelProfile(BaseModel):
    """A configured model the router may choose. `alias` is what the core refers to."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    alias: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    provider: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    model_id: str = Field(min_length=1, max_length=128)
    capabilities: ModelCapabilities
    runs_locally: bool = False
    priority: int = Field(default=100, ge=0)
