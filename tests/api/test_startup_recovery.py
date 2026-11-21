"""Starting the API sends an approval's actions that a crash left unsent."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from incidentops.api.app import create_app
from incidentops.core.config import Settings
from incidentops.workflows.incident import IncidentService
from tests.conftest import load_fixture

WEBHOOK = "https://n8n.example/hook"


class Crash(BaseException):
    pass


def test_the_api_sends_a_crashed_approvals_actions_on_startup(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = settings.model_copy(
        update={"n8n_action_webhook_url": WEBHOOK, "incidentops_api_key": "k"}
    )
    payload = load_fixture("incident_complete.json")
    run_id = payload["run_id"]

    def dies(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise Crash

    crashed = IncidentService(configured, dispatcher=dies)
    try:
        crashed.start(payload)
        with pytest.raises(Crash):
            crashed.decide(run_id, run_id, "approved")
    finally:
        crashed.close()

    sent: list[str] = []
    monkeypatch.setattr(
        "incidentops.workflows.incident.service.request_n8n_actions",
        lambda url, body, **kwargs: sent.append(url) or {},
    )
    with TestClient(create_app(configured)) as client:
        incident = client.get(f"/incidents/{run_id}", headers={"X-API-Key": "k"}).json()

    assert sent == [WEBHOOK]
    assert incident["action_dispatch"] == "sent" and incident["status"] == "succeeded"
