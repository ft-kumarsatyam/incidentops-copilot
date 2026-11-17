"""Redact secrets and obvious PII before anything is stored or sent to a model."""

import re
from typing import Any

REDACTED = "[REDACTED]"

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), "[REDACTED_SLACK_TOKEN]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"), "Bearer [REDACTED]"),
    (
        re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key)\b(\s*[:=]\s*)\S+"),
        r"\1\2" + REDACTED,
    ),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[REDACTED_EMAIL]"),
)

_SENSITIVE_KEYS = re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key|authorization)")


def redact_text(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact(value: Any) -> Any:
    """Recursively redact strings; values under sensitive-looking keys are fully masked."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, dict):
        return {
            key: REDACTED
            if isinstance(key, str) and _SENSITIVE_KEYS.search(key) and value[key] not in (None, "")
            else redact(value[key])
            for key in value
        }
    return value
