"""POST /decisions and GET /decisions/status: RBAC, tenant allow-list, audit and redaction."""

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from incidentops.api.app import create_app
from incidentops.core.config import Settings
from tests.api.identity import SHARED, headers

BODY = {
    "state": {"note": "password=hunter2 checkout is down"},
    "questions": {"urgent": {"type": "noul", "instructions": "Is this urgent?"}},
}
RESPONSE = {
    "model": "jev-1.13.0",
    "answers": {"urgent": {"type": "noul", "noul": 0.93}},
    "usage": {"input_tokens": 30, "output_tokens": 2},
}


def _make(settings: Settings, handler: Any, **changes: Any) -> Iterator[TestClient]:
    secured = settings.model_copy(
        update={
            "incidentops_api_key": "shared-secret",
            "jev_enabled": True,
            "jev_api_key": "jev-key",
            **changes,
        }
    )
    with TestClient(create_app(secured)) as client:
        jev = client.app.state.service.jev  # type: ignore[attr-defined]
        jev._http = httpx.Client(
            base_url="https://jev.test",
            headers={"Authorization": "Bearer jev-key"},
            transport=httpx.MockTransport(handler),
        )
        yield client


@pytest.fixture
def sent() -> list[bytes]:
    return []


@pytest.fixture
def client(settings: Settings, sent: list[bytes]) -> Iterator[TestClient]:
    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.content)
        return httpx.Response(200, json=RESPONSE)

    yield from _make(settings, handler)


def _events(client: TestClient, tenant: str = "acme") -> list[dict[str, Any]]:
    rows, _ = client.app.state.service.repo.audit_search(tenant=tenant, limit=100)  # type: ignore[attr-defined]
    return rows


@pytest.mark.parametrize(
    ("role", "allowed"), [("viewer", False), ("responder", True), ("admin", True)]
)
def test_invoking_needs_the_responder_role(client: TestClient, role: str, allowed: bool) -> None:
    response = client.post("/decisions", json=BODY, headers=headers(client, role, [role]))

    assert response.status_code == (200 if allowed else 403)
    if allowed:
        assert response.json()["answers"]["urgent"]["noul"] == 0.93


def test_a_denied_call_is_audited_and_never_reaches_jev(
    client: TestClient, sent: list[bytes]
) -> None:
    client.post("/decisions", json=BODY, headers=headers(client, "v", ["viewer"]))

    assert sent == []
    [event] = [e for e in _events(client) if e["event"] == "auth:forbidden"]
    assert event["detail"]["permission"] == "decision:invoke"


def test_no_credentials_is_a_401(client: TestClient) -> None:
    assert client.post("/decisions", json=BODY).status_code == 401


def test_the_audit_records_who_and_what_it_cost_but_not_the_text(client: TestClient) -> None:
    client.post("/decisions", json=BODY, headers=headers(client, "robot", ["responder"]))

    [event] = [e for e in _events(client) if e["event"] == "decision:invoked"]
    assert event["detail"] == {
        "model": "jev-1.13.0",
        "questions": ["urgent"],
        "input_tokens": 30,
        "output_tokens": 2,
    }
    assert "hunter2" not in json.dumps(event) and "checkout is down" not in json.dumps(event)


def test_secrets_in_the_state_never_leave(client: TestClient, sent: list[bytes]) -> None:
    client.post("/decisions", json=BODY, headers=headers(client, "r", ["responder"]))

    assert b"hunter2" not in sent[0] and b"checkout is down" in sent[0]


@pytest.mark.parametrize(
    "questions",
    [
        {},
        {"q": {"type": "choice", "instructions": "x"}},  # choice needs criteria
        {"q": {"type": "score", "instructions": "x", "criteria": {"a": "b"}}},  # score needs a list
        {"q": {"type": "noul", "instructions": "x", "criteria": {"maybe": "y"}}},
        {"q": {"type": "box", "instructions": "x"}},  # images are not offered
        {"bad name!": {"type": "noul", "instructions": "x"}},
        {f"q{i}": {"type": "noul", "instructions": "x"} for i in range(9)},
    ],
)
def test_malformed_questions_are_rejected_before_any_call(
    client: TestClient, sent: list[bytes], questions: dict[str, Any]
) -> None:
    response = client.post(
        "/decisions",
        json={"state": "x", "questions": questions},
        headers=headers(client, "r", ["responder"]),
    )

    assert response.status_code == 422 and sent == []


def test_the_caller_cannot_choose_the_model(client: TestClient, sent: list[bytes]) -> None:
    client.post(
        "/decisions",
        json={**BODY, "model": "jev-expensive"},
        headers=headers(client, "r", ["responder"]),
    )

    assert json.loads(sent[0])["model"] == "jev-latest"


def test_a_jev_failure_is_a_502_and_is_audited(settings: Settings) -> None:
    gen = _make(settings, lambda request: httpx.Response(500, json={}))
    client = next(gen)
    response = client.post("/decisions", json=BODY, headers=headers(client, "r", ["responder"]))

    assert response.status_code == 502
    assert response.json() == {"detail": "the decision model call failed"}
    assert any(e["event"] == "decision:failed" for e in _events(client))
    gen.close()


def test_only_allowed_tenants_may_call(settings: Settings) -> None:
    gen = _make(
        settings, lambda request: httpx.Response(200, json=RESPONSE), jev_allowed_tenants=["acme"]
    )
    client = next(gen)

    assert (
        client.post("/decisions", json=BODY, headers=headers(client, "a", ["admin"])).status_code
        == 200
    )
    other = headers(client, "b", ["admin"], tenant="globex")
    assert client.post("/decisions", json=BODY, headers=other).status_code == 403
    assert any(e["event"] == "decision:forbidden" for e in _events(client, "globex"))
    gen.close()


def test_status_is_admin_only_and_counts_usage(client: TestClient) -> None:
    client.post("/decisions", json=BODY, headers=headers(client, "r", ["responder"]))

    assert (
        client.get("/decisions/status", headers=headers(client, "r2", ["approver"])).status_code
        == 403
    )
    status = client.get("/decisions/status", headers=headers(client, "a", ["admin"])).json()
    assert status["enabled"] is True and status["calls"] == 1 and status["input_tokens"] == 30
    assert "jev-key" not in json.dumps(status)


def test_the_shared_key_is_an_admin_so_it_can_call(client: TestClient) -> None:
    assert client.post("/decisions", json=BODY, headers=SHARED).status_code == 200


def test_when_jev_is_off_the_route_says_so(settings: Settings) -> None:
    secured = settings.model_copy(update={"incidentops_api_key": "shared-secret"})
    with TestClient(create_app(secured)) as client:
        assert client.post("/decisions", json=BODY, headers=SHARED).status_code == 503
        status = client.get("/decisions/status", headers=SHARED).json()
        assert status["enabled"] is False and status["calls"] == 0


def test_me_lists_the_new_permissions(client: TestClient) -> None:
    me = client.get("/me", headers=headers(client, "r", ["responder"])).json()

    assert "decision:invoke" in me["permissions"] and "decision:admin" not in me["permissions"]
