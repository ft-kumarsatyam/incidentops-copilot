"""Normalize vendor-specific monitoring payloads into evidence."""

import math
from typing import Any

MAX_TEXT = 300
_ALLOWED = ("service", "error_rate", "latency_ms", "alert", "severity", "timed_out")


def parse_error_rate(value: Any) -> float | None:
    """Return an error rate as a 0-1 ratio, or ``None`` when the value cannot be trusted.

    Accepts ``0.12``, ``"0.12"``, ``"12%"`` and ``12`` (a bare number above 1 is read as a
    percentage). Booleans, NaN, infinity, negatives, text and anything above 100% are rejected
    rather than guessed at, because a wrong severity is worse than an unverified one.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip()
        is_percent = text.endswith("%")
        try:
            number = float(text.removesuffix("%").strip())
        except ValueError:
            return None
    elif isinstance(value, int | float):
        is_percent = False
        number = float(value)
    else:
        return None
    if not math.isfinite(number) or number < 0:
        return None
    if is_percent or number > 1:  # "12%" and a bare 12 both mean twelve percent
        number /= 100
    return number if number <= 1 else None


def _scalar(value: Any) -> Any:
    """Keep only plain scalars, so nested vendor payloads cannot smuggle text to the model."""
    if isinstance(value, str):
        return " ".join(value.split())[:MAX_TEXT]
    if isinstance(value, bool | int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    return None


def normalize_monitoring(payload: dict[str, Any]) -> dict[str, Any]:
    """Allowlist keys, coerce values to scalars, and flag unusable measurements.

    ``timed_out`` is the single "impact is unverified" signal the rest of the system already
    understands, so an unparseable ``error_rate`` sets it instead of silently becoming 0%.
    """
    if not payload or not isinstance(payload, dict):
        return {}
    result: dict[str, Any] = {}
    for key in _ALLOWED:
        if key not in payload or key == "error_rate":
            continue
        value = _scalar(payload[key])
        if value is not None:
            result[key] = value
    if "error_rate" in payload:
        rate = parse_error_rate(payload["error_rate"])
        if rate is None:
            result["timed_out"] = True
            result["unverified_reason"] = "error_rate was missing, malformed or out of range"
        else:
            result["error_rate"] = rate
    return result
