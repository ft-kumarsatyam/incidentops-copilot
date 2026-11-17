"""Redact secrets and obvious PII before anything is stored or sent to a model."""

import re
from typing import Any

REDACTED = "[REDACTED]"

# Every quantifier that can run over attacker-controlled text is bounded. Unbounded `+`/`*` in
# front of a literal that may be absent is quadratic, and this runs before any length cap.
# Order matters: multi-line and URL-embedded secrets go first so the narrower patterns below
# (email, key=value) cannot mangle them into something half-redacted.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY(?: BLOCK)?-----.*?(?:-----END [A-Z ]*PRIVATE KEY"
            r"(?: BLOCK)?-----|\Z)",
            re.DOTALL,
        ),
        "[REDACTED_PRIVATE_KEY]",
    ),
    # scheme://user:password@host  ->  keep the user, drop the password
    (
        re.compile(r"\b([a-zA-Z][a-zA-Z0-9+.-]{0,31}://[^\s/:@]{1,256}):[^\s/@]{1,256}@"),
        r"\1:" + REDACTED + "@",
    ),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), "[REDACTED_SLACK_TOKEN]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "[REDACTED_GOOGLE_KEY]"),
    (re.compile(r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"\bwhsec_[A-Za-z0-9]{16,}\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), "[REDACTED_API_KEY]"),
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
        "[REDACTED_JWT]",
    ),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"), "Bearer [REDACTED]"),
    (re.compile(r"(?i)\bbasic\s+[A-Za-z0-9+/]{12,}={0,2}"), "Basic [REDACTED]"),
    (
        re.compile(
            r"(?i)\b(password|passwd|pass|pwd|secret|token|api[_-]?key|private[_-]?key"
            r"|client[_-]?secret|credentials?)\b(\s*[:=]\s*)\S+"
        ),
        r"\1\2" + REDACTED,
    ),
    (re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b"), "[REDACTED_SSN]"),
    (re.compile(r"[\w.+-]{1,64}@[\w-]{1,255}\.[\w.-]{1,255}"), "[REDACTED_EMAIL]"),
)

# Candidate card numbers: 13-19 digits, optionally grouped by single spaces or dashes. A match
# is only redacted when it passes the Luhn check *and* looks like a real card network, so epoch
# timestamps, commit ids and ticket numbers survive.
_CARD = re.compile(r"(?<![\w.])(?:\d[ -]?){12,18}\d(?![\w.])")
_CARD_PREFIX = re.compile(r"^(?:4|5[1-5]|2[2-7]|3[47]|6011|65|35)")

_SENSITIVE_KEYS = re.compile(
    r"(?i)(password|passwd|secret|token|api[_-]?key|authorization|cookie|credential"
    r"|private[_-]?key|ssn|card[_-]?number)"
)


def _luhn_ok(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value = value * 2 - 9 if value > 4 else value * 2
        total += value
    return total % 10 == 0


def _redact_card(match: re.Match[str]) -> str:
    digits = re.sub(r"\D", "", match.group())
    if 13 <= len(digits) <= 19 and _CARD_PREFIX.match(digits) and _luhn_ok(digits):
        return "[REDACTED_CARD]"
    return match.group()


def redact_text(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return _CARD.sub(_redact_card, text)


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
