from typing import Any

import httpx
import pytest

from incidentops.tools.n8n import request_actions

PAYLOAD = {"approval": "approved", "idempotency_key": "k"}


class FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status

    def raise_for_status(self) -> None:
        if self.status >= 400:
            request = httpx.Request("POST", "https://n8n")
            raise httpx.HTTPStatusError(
                "bad", request=request, response=httpx.Response(self.status, request=request)
            )

    def json(self) -> dict[str, Any]:
        return {"ok": True}


def _patch_post(monkeypatch: pytest.MonkeyPatch, statuses: list[int]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    def post(url: str, **kwargs: Any) -> FakeResponse:
        calls.append(kwargs)
        return FakeResponse(statuses.pop(0))

    monkeypatch.setattr(httpx, "post", post)
    return calls


def test_retries_transient_failures_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_post(monkeypatch, [503, 502, 200])

    assert request_actions("https://n8n", PAYLOAD, attempts=3, backoff=0) == {"ok": True}
    assert len(calls) == 3
    assert calls[0]["headers"]["Idempotency-Key"] == "k"


def test_does_not_retry_client_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_post(monkeypatch, [400, 200])

    with pytest.raises(httpx.HTTPStatusError):
        request_actions("https://n8n", PAYLOAD, attempts=3, backoff=0)
    assert len(calls) == 1


def test_gives_up_after_the_configured_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_post(monkeypatch, [500, 500, 500, 200])

    with pytest.raises(httpx.HTTPStatusError):
        request_actions("https://n8n", PAYLOAD, attempts=3, backoff=0)
    assert len(calls) == 3


def test_refuses_unapproved_payloads() -> None:
    with pytest.raises(ValueError, match="explicit approval"):
        request_actions("https://n8n", {"approval": None, "idempotency_key": "k"})
