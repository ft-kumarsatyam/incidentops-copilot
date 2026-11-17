"""Normalize vendor-specific monitoring payloads into evidence."""

from typing import Any


def normalize_monitoring(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload:
        return {}
    allowed = ("service", "error_rate", "latency_ms", "alert", "severity", "timed_out")
    return {key: payload[key] for key in allowed if key in payload}
