"""Write actions are delegated to n8n after human approval."""

from typing import Any

import httpx


def request_actions(
    webhook_url: str,
    payload: dict[str, Any],
    timeout: float = 10.0,
) -> dict[str, Any]:
    if payload.get("approval") != "approved":
        raise ValueError("n8n actions require explicit approval")
    response = httpx.post(
        webhook_url,
        json=payload,
        headers={"Idempotency-Key": str(payload["idempotency_key"])},
        timeout=timeout,
    )
    response.raise_for_status()
    result = response.json()
    if not isinstance(result, dict):
        raise ValueError("n8n action webhook must return a JSON object")
    return result
