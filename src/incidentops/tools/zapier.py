"""Dispatch approved incidents to a Webhooks by Zapier Catch Hook."""

from typing import Any

from incidentops.tools.webhooks import post_json


def request_actions(
    webhook_url: str,
    payload: dict[str, Any],
    *,
    timeout: float = 10.0,
    attempts: int = 3,
    backoff: float = 0.5,
    token: str | None = None,
    signing_secret: str | None = None,
) -> dict[str, Any]:
    """POST a flat, Zapier-mapping-friendly event with retry and idempotency metadata."""
    if payload.get("approval") != "approved":
        raise ValueError("Zapier actions require explicit approval")

    key = str(payload["idempotency_key"])
    event = {
        "event": "incident.actions_approved",
        "idempotency_key": key,
        "run_id": payload["run_id"],
        "status": payload.get("status"),
        "severity": payload.get("severity"),
        "service": payload.get("service"),
        "summary": payload.get("summary"),
        "owner": payload.get("owner"),
        "approved_actions": payload.get("approved_actions", []),
        "incident": payload,
    }
    headers = {
        "Idempotency-Key": key,
        "X-IncidentOps-Event": "incident.actions_approved",
    }
    if token:
        headers["X-IncidentOps-Token"] = token
    return post_json(
        webhook_url,
        event,
        headers=headers,
        timeout=timeout,
        attempts=attempts,
        backoff=backoff,
        signing_secret=signing_secret,
    )
