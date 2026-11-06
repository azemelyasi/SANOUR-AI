"""Deterministic routing: filter by hard requirements, then pick the first by (priority, alias).

Exactly one profile is selected per request. There is no fallback list.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

from sanour_ai.errors import ErrorCode, SanourError
from sanour_ai.models.capabilities import ModelProfile
from sanour_ai.models.requests import SanourRequest


def estimate_tokens(text: str) -> int:
    """Rough estimate of about 4 characters per token. Not a real tokenizer."""
    return math.ceil(len(text) / 4)


def _fits(profile: ModelProfile, request: SanourRequest, needed_tokens: int) -> bool:
    caps = profile.capabilities
    if not caps.text_generation:
        return False
    if caps.max_output_tokens < request.max_output_tokens:
        return False
    if caps.max_context_tokens < needed_tokens:
        return False
    return profile.runs_locally or not request.offline_only


def _has_required(profile: ModelProfile, required: tuple[str, ...]) -> bool:
    """True when the profile supports every requested capability. Empty means no requirement."""
    return all(getattr(profile.capabilities, name) for name in required)


class DeterministicRouter:
    def __init__(self, profiles: Iterable[ModelProfile]) -> None:
        ordered = list(profiles)
        aliases = [profile.alias for profile in ordered]
        if len(aliases) != len(set(aliases)):
            raise ValueError("duplicate model aliases in router configuration")
        self._profiles = tuple(sorted(ordered, key=lambda p: (p.priority, p.alias)))

    @property
    def profiles(self) -> tuple[ModelProfile, ...]:
        return self._profiles

    def select(self, request: SanourRequest) -> ModelProfile:
        """Return the single profile this request will use."""
        needed = estimate_tokens(request.prompt) + request.max_output_tokens
        for profile in self._profiles:
            if _fits(profile, request, needed) and _has_required(
                profile, request.required_capabilities
            ):
                return profile
        raise SanourError(
            ErrorCode.NO_ELIGIBLE_MODEL, "no configured model can handle this request"
        )
