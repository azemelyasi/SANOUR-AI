"""Accepts raw input, validates it, and attaches the request identifier."""

from __future__ import annotations

from pydantic import ValidationError

from sanour_ai.errors import ErrorCode, SanourError
from sanour_ai.models.requests import SanourRequest, SanourRequestInput


def accept(raw: object, request_id: str) -> SanourRequest:
    try:
        validated = SanourRequestInput.model_validate(raw)
    except ValidationError as exc:
        known = SanourRequestInput.model_fields
        names = set()
        for err in exc.errors():
            first = str(err["loc"][0]) if err["loc"] else ""
            names.add(first if first in known else "unknown_field")
        ordered = sorted(names)
        # Only field names are reported, never values, so secrets in input are not echoed.
        raise SanourError(
            ErrorCode.INVALID_REQUEST, f"invalid request fields: {', '.join(ordered) or 'request'}"
        ) from None
    return SanourRequest.model_validate({**validated.model_dump(), "request_id": request_id})
