"""Write actions are delegated to n8n after human approval."""

from typing import Any

import httpx
from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential


def _is_transient(error: BaseException) -> bool:
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code >= 500 or error.response.status_code == 429
    return isinstance(error, httpx.TransportError)


def request_actions(
    webhook_url: str,
    payload: dict[str, Any],
    *,
    timeout: float = 10.0,
    attempts: int = 3,
    backoff: float = 0.5,
) -> dict[str, Any]:
    """POST the approved incident to n8n, retrying transient failures with backoff."""
    if payload.get("approval") != "approved":
        raise ValueError("n8n actions require explicit approval")

    def post() -> httpx.Response:
        response = httpx.post(
            webhook_url,
            json=payload,
            headers={"Idempotency-Key": str(payload["idempotency_key"])},
            timeout=timeout,
        )
        response.raise_for_status()
        return response

    retrying = Retrying(
        stop=stop_after_attempt(max(1, attempts)),
        wait=wait_exponential(multiplier=backoff, max=8),
        retry=retry_if_exception(_is_transient),
        reraise=True,
    )
    result = retrying(post).json()
    if not isinstance(result, dict):
        raise ValueError("n8n action webhook must return a JSON object")
    return result
