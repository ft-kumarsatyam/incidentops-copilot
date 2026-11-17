import json
from pathlib import Path

from fastapi.testclient import TestClient

from workflow_automation.api import create_app
from workflow_automation.config import Settings

FIXTURES = Path(__file__).parent / "fixtures"


def test_authenticated_incident_and_decision_round_trip(tmp_path: Path) -> None:
    settings = Settings(checkpoint_dir=tmp_path, incidentops_api_key="test-secret")
    client = TestClient(create_app(settings))
    incident = json.loads((FIXTURES / "incident_complete.json").read_text(encoding="utf-8"))

    assert client.get("/health").json() == {"status": "ok"}
    assert client.post("/incidents", json=incident).status_code == 401

    created = client.post(
        "/incidents",
        json=incident,
        headers={"X-API-Key": "test-secret"},
    )
    assert created.status_code == 200
    assert created.json()["status"] == "awaiting_approval"
    assert "__interrupt__" not in created.json()

    decided = client.post(
        f"/incidents/{incident['run_id']}/decision",
        json={"idempotency_key": incident["run_id"], "decision": "approved"},
        headers={"X-API-Key": "test-secret"},
    )
    assert decided.status_code == 200
    assert decided.json()["status"] == "succeeded"
    assert decided.json()["action_dispatch"] == "not_configured"


def test_decision_rejects_mismatched_idempotency_key(tmp_path: Path) -> None:
    settings = Settings(checkpoint_dir=tmp_path)
    client = TestClient(create_app(settings))
    incident = json.loads((FIXTURES / "incident_complete.json").read_text(encoding="utf-8"))
    client.post("/incidents", json=incident)

    response = client.post(
        f"/incidents/{incident['run_id']}/decision",
        json={"idempotency_key": "wrong", "decision": "approved"},
    )
    assert response.status_code == 409


def test_dashboard_lists_checkpointed_incidents(tmp_path: Path) -> None:
    settings = Settings(checkpoint_dir=tmp_path, incidentops_api_key="dashboard-key")
    client = TestClient(create_app(settings))
    incident = json.loads((FIXTURES / "incident_complete.json").read_text(encoding="utf-8"))
    headers = {"X-API-Key": "dashboard-key"}
    client.post("/incidents", json=incident, headers=headers)

    page = client.get("/")
    listing = client.get("/incidents", headers=headers)
    detail = client.get(f"/incidents/{incident['run_id']}", headers=headers)
    stylesheet = client.get("/assets/app.css")

    assert page.status_code == 200
    assert "Response board" in page.text
    assert stylesheet.status_code == 200
    assert listing.json()[0]["run_id"] == incident["run_id"]
    assert detail.json()["status"] == "awaiting_approval"


def test_dashboard_approval_dispatches_to_n8n(tmp_path: Path, monkeypatch) -> None:
    dispatched = []
    monkeypatch.setattr(
        "workflow_automation.api.request_actions",
        lambda url, payload: dispatched.append((url, payload["run_id"])) or {"ok": True},
    )
    settings = Settings(
        checkpoint_dir=tmp_path,
        n8n_action_webhook_url="https://n8n.example/webhook/incidentops/actions",
    )
    client = TestClient(create_app(settings))
    incident = json.loads((FIXTURES / "incident_complete.json").read_text(encoding="utf-8"))
    client.post("/incidents", json=incident)

    response = client.post(
        f"/incidents/{incident['run_id']}/decision",
        json={"idempotency_key": incident["run_id"], "decision": "approved"},
    )

    assert response.json()["action_dispatch"] == "sent"
    assert dispatched == [(settings.n8n_action_webhook_url, incident["run_id"])]

