"""Model catalog: an immutable, validated set of ModelProfiles.

Configuration only. It does not route, fetch models, or call providers.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from sanour_ai.models.capabilities import ModelProfile


class ModelCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    profiles: tuple[ModelProfile, ...] = Field(min_length=1)

    @field_validator("profiles")
    @classmethod
    def _aliases_are_unique(cls, profiles: tuple[ModelProfile, ...]) -> tuple[ModelProfile, ...]:
        # Input order decides which duplicate is reported, so the result is deterministic.
        seen: set[str] = set()
        for profile in profiles:
            if profile.alias in seen:
                raise ValueError(f"duplicate model alias: {profile.alias}")
            seen.add(profile.alias)
        return profiles
