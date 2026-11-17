from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from incidentops.api.app import create_app
from incidentops.core.config import Settings
from tests.conftest import load_fixture

KEY = {"X-API-Key": "test-secret"}


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    secured = settings.model_copy(update={"incidentops_api_key": "test-secret"})
    with TestClient(create_app(secured)) as test_client:
        yield test_client


def test_authenticated_incident_and_decision_round_trip(client: TestClient) -> None:
    incident = load_fixture("incident_complete.json")

    assert client.get("/health").json() == {"status": "ok"}
    assert client.post("/incidents", json=incident).status_code == 401
    assert client.get("/incidents").status_code == 401

    created = client.post("/incidents", json=incident, headers=KEY)
    assert created.status_code == 200
    assert created.json()["status"] == "awaiting_approval"
    assert "X-Request-ID" in created.headers

    decided = client.post(
        f"/incidents/{incident['run_id']}/decision",
        json={"idempotency_key": incident["run_id"], "decision": "approved"},
        headers={**KEY, "X-Actor": "alice"},
    )
    assert decided.status_code == 200
    assert decided.json()["status"] == "succeeded"
    assert decided.json()["action_dispatch"] == "not_configured"

    audit = client.get(f"/incidents/{incident['run_id']}/audit", headers=KEY).json()
    assert [entry["actor"] for entry in audit] == ["api", "alice", "alice"]


def test_decision_conflicts(client: TestClient) -> None:
    incident = load_fixture("incident_complete.json")
    client.post("/incidents", json=incident, headers=KEY)
    path = f"/incidents/{incident['run_id']}/decision"

    wrong_key = client.post(
        path, json={"idempotency_key": "x", "decision": "approved"}, headers=KEY
    )
    assert wrong_key.status_code == 409

    body = {"idempotency_key": incident["run_id"], "decision": "approved"}
    assert client.post(path, json=body, headers=KEY).status_code == 200
    again = client.post(path, json=body, headers=KEY)
    assert again.status_code == 409
    assert "not awaiting approval" in again.json()["detail"]


def test_unknown_run_and_invalid_run_id(client: TestClient) -> None:
    assert client.get("/incidents/missing", headers=KEY).status_code == 404
    assert client.get("/incidents/bad id!", headers=KEY).status_code == 422
    payload = {**load_fixture("incident_complete.json"), "run_id": "../../etc/passwd"}
    assert client.post("/incidents", json=payload, headers=KEY).status_code == 422


def test_list_supports_filter_search_and_total_header(client: TestClient) -> None:
    client.post("/incidents", json=load_fixture("incident_complete.json"), headers=KEY)
    client.post("/incidents", json=load_fixture("incident_ambiguous.json"), headers=KEY)

    everything = client.get("/incidents", headers=KEY)
    assert everything.headers["X-Total-Count"] == "2"

    needs_human = client.get("/incidents?status=needs_human", headers=KEY)
    assert [item["status"] for item in needs_human.json()] == ["needs_human"]

    search = client.get("/incidents?q=checkout", headers=KEY)
    assert search.headers["X-Total-Count"] == "1"
    assert client.get("/incidents?limit=1", headers=KEY).json().__len__() == 1


def test_secrets_in_incident_text_are_redacted_before_storage(client: TestClient) -> None:
    incident = {
        **load_fixture("incident_complete.json"),
        "summary": "Checkout failing, token=abc123secret was pasted in the thread",
    }
    created = client.post("/incidents", json=incident, headers=KEY).json()

    assert "abc123secret" not in created["summary"]


def test_ready_reports_dependencies(client: TestClient, settings: Settings) -> None:
    ready = client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["checks"]["database"] == "ok"

    settings.runbook_db.unlink()
    assert client.get("/ready").status_code == 503


def test_dashboard_is_served(client: TestClient) -> None:
    assert client.get("/").status_code == 200
    assert client.get("/assets/app.css").status_code == 200


def test_oversized_bodies_are_rejected(settings: Settings) -> None:
    small = settings.model_copy(update={"max_body_bytes": 50})
    with TestClient(create_app(small)) as client:
        response = client.post("/incidents", json=load_fixture("incident_complete.json"))
    assert response.status_code == 413


def test_rate_limit_returns_429(settings: Settings) -> None:
    limited = settings.model_copy(update={"rate_limit_per_minute": 2})
    with TestClient(create_app(limited)) as client:
        codes = [client.get("/incidents").status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_api_refuses_to_start_without_a_key_outside_dev(settings: Settings) -> None:
    prod = settings.model_copy(update={"incidentops_env": "prod", "incidentops_api_key": None})
    with pytest.raises(RuntimeError, match="INCIDENTOPS_API_KEY"):
        create_app(prod)


def test_cors_allows_only_configured_origins(settings: Settings) -> None:
    configured = settings.model_copy(update={"cors_origins": ["http://localhost:5173"]})
    with TestClient(create_app(configured)) as client:
        allowed = client.get("/health", headers={"Origin": "http://localhost:5173"})
        other = client.get("/health", headers={"Origin": "http://evil.example"})
    assert allowed.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "access-control-allow-origin" not in other.headers


def test_failed_dispatch_surfaces_and_can_be_retried(settings: Settings, monkeypatch: Any) -> None:
    outcomes: list[Exception | None] = [httpx.ConnectError("down"), None]

    def dispatcher(*args: Any, **kwargs: Any) -> dict[str, Any]:
        outcome = outcomes.pop(0)
        if outcome:
            raise outcome
        return {}

    wired = settings.model_copy(update={"n8n_action_webhook_url": "https://n8n.example/a"})
    app = create_app(wired)
    app.state.service._dispatcher = dispatcher
    incident = load_fixture("incident_complete.json")
    with TestClient(app) as client:
        client.post("/incidents", json=incident)
        body = {"idempotency_key": incident["run_id"], "decision": "approved"}
        failed = client.post(f"/incidents/{incident['run_id']}/decision", json=body)
        assert failed.status_code == 200
        assert failed.json()["status"] == "failed"

        retried = client.post(f"/incidents/{incident['run_id']}/retry-dispatch")
        assert retried.json()["status"] == "succeeded"
        assert retried.json()["action_dispatch"] == "sent"
