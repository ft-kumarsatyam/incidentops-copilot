from incidentops.workflows.incident import IncidentService
from tests.conftest import load_fixture


def test_incident_actions_cite_retrieved_runbook(service: IncidentService) -> None:
    result = service.start(load_fixture("incident_complete.json"))

    assert result["runbook_hits"][0]["path"] == "checkout-api.md"
    assert any("checkout-api.md" in action for action in result["proposed_actions"])
