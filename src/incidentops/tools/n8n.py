"""Write actions are delegated to n8n after human approval."""

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
) -> dict[str, Any]:
    """POST the approved incident to n8n, retrying transient failures with backoff."""
    if payload.get("approval") != "approved":
        raise ValueError("n8n actions require explicit approval")

    headers = {"Idempotency-Key": str(payload["idempotency_key"])}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    return post_json(
        webhook_url,
        payload,
        headers=headers,
        timeout=timeout,
        attempts=attempts,
        backoff=backoff,
    )
