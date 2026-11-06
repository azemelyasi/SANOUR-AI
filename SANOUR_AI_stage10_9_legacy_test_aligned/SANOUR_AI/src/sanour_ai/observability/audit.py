"""Audit events. Fields are whitelisted, so prompts and other content never reach the log."""

from __future__ import annotations

import json
import logging
from typing import Any

from sanour_ai.security.redaction import RedactingFilter, redact

AUDIT_LOGGER_NAME = "sanour_ai.audit"

_ALLOWED_FIELDS = frozenset(
    {
        "request_id",
        "task_type",
        "model_alias",
        "model_id",
        "status",
        "error_code",
        "retryable",
        "latency_ms",
        "exc_type",
    }
)


class AuditLogger:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger(AUDIT_LOGGER_NAME)
        if self._logger.level == logging.NOTSET:
            self._logger.setLevel(logging.INFO)
        if not any(isinstance(f, RedactingFilter) for f in self._logger.filters):
            self._logger.addFilter(RedactingFilter())

    def emit(self, event: str, **fields: Any) -> None:
        safe = {key: value for key, value in fields.items() if key in _ALLOWED_FIELDS}
        self._logger.info("%s %s", redact(event), json.dumps(safe, sort_keys=True, default=str))
