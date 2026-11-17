from typing import Any

import httpx
import pytest

from incidentops.tools.zapier import request_actions

PAYLOAD = {
    "approval": "approved",
    "idempotency_key": "inc-1",
    "run_id": "inc-1",
    "status": "succeeded",
    "severity": "SEV-2",
    "service": "auth-api",
    "summary": "Sign-in failures",
    "owner": "identity-oncall",
    "approved_actions": ["Open an incident issue"],
}


class FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status

    def raise_for_status(self) -> None:
        if self.status >= 400:
            request = httpx.Request("POST", "https://hooks.zapier.com")
            raise httpx.HTTPStatusError(
                "bad", request=request, response=httpx.Response(self.status, request=request)
            )

    def json(self) -> dict[str, Any]:
        return {"status": "success"}


def test_sends_zapier_friendly_event_and_idempotency_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def post(url: str, **kwargs: Any) -> FakeResponse:
        calls.append(kwargs)
        return FakeResponse(200)

    monkeypatch.setattr(httpx, "post", post)

    result = request_actions("https://hooks.zapier.com/catch/1/abc", PAYLOAD, token="secret")

    assert result == {"status": "success"}
    assert calls[0]["json"]["event"] == "incident.actions_approved"
    assert calls[0]["json"]["approved_actions"] == ["Open an incident issue"]
    assert calls[0]["headers"]["Idempotency-Key"] == "inc-1"
    assert calls[0]["headers"]["X-IncidentOps-Token"] == "secret"


def test_retries_transient_zapier_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    statuses = [503, 200]
    calls: list[int] = []

    def post(url: str, **kwargs: Any) -> FakeResponse:
        calls.append(1)
        return FakeResponse(statuses.pop(0))

    monkeypatch.setattr(httpx, "post", post)

    request_actions("https://hooks.zapier.com/catch/1/abc", PAYLOAD, attempts=2, backoff=0)

    assert len(calls) == 2


def test_refuses_unapproved_zapier_payload() -> None:
    with pytest.raises(ValueError, match="explicit approval"):
        request_actions(
            "https://hooks.zapier.com/catch/1/abc",
            {**PAYLOAD, "approval": "rejected"},
        )
