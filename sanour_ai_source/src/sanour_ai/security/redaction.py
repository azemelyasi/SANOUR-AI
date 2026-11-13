"""Secret redaction for logs and any text that might contain credentials."""

from __future__ import annotations

import logging
import re

REDACTED = "[REDACTED]"

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # OpenAI-style secret keys (word boundary keeps words like "risk-analysis" intact).
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"), REDACTED),
    # key=value or key: value pairs, optionally with a "Bearer " prefix on the value.
    (
        re.compile(
            r"(?i)\b(api[_-]?key|secret|token|password|passwd|authorization)\b"
            r"(\s*[:=]\s*)(?:bearer\s+)?\S+"
        ),
        r"\1\2" + REDACTED,
    ),
)


def redact(text: str) -> str:
    """Return text with known secret shapes replaced by a placeholder."""
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFilter(logging.Filter):
    """Redacts the formatted message and drops tracebacks, which can carry secrets."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True
