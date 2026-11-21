"""Shared reliable JSON webhook transport."""

import json
from typing import Any

import httpx
from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential

from incidentops.core.signing import signature_headers


def is_transient(error: BaseException) -> bool:
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
    signing_secret: str | None = None,
) -> dict[str, Any]:
    # Signing needs the exact bytes on the wire, so a signed request sends pre-encoded content.
    body = json.dumps(payload, separators=(",", ":"), default=str).encode()

    def post() -> httpx.Response:
        if signing_secret:
            # Re-signed on every attempt so a retry after backoff still has a fresh timestamp.
            response = httpx.post(
                webhook_url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    **headers,
                    **signature_headers(body, signing_secret),
                },
                timeout=timeout,
            )
        else:
            response = httpx.post(webhook_url, json=payload, headers=headers, timeout=timeout)
        response.raise_for_status()
        return response

    retrying = Retrying(
        stop=stop_after_attempt(max(1, attempts)),
        wait=wait_exponential(multiplier=backoff, max=8),
        retry=retry_if_exception(is_transient),
        reraise=True,
    )
    result = retrying(post).json()
    if not isinstance(result, dict):
        raise ValueError("action webhook must return a JSON object")
    return result
