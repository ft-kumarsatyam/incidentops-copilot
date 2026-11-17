"""Shared reliable JSON webhook transport."""

from typing import Any

import httpx
from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential


def _is_transient(error: BaseException) -> bool:
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code >= 500 or error.response.status_code == 429
    return isinstance(error, httpx.TransportError)


def post_json(
    webhook_url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str],
    timeout: float,
    attempts: int,
    backoff: float,
) -> dict[str, Any]:
    def post() -> httpx.Response:
        response = httpx.post(webhook_url, json=payload, headers=headers, timeout=timeout)
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
        raise ValueError("action webhook must return a JSON object")
    return result
