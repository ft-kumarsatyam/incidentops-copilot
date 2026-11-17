from incidentops.core.config import Settings
from incidentops.workflows.incident import IncidentService
from tests.conftest import load_fixture


def test_complete_incident_pauses_for_approval(service: IncidentService) -> None:
    result = service.start(load_fixture("incident_complete.json"))

    assert result["status"] == "awaiting_approval"
    assert result["severity"] == "SEV-1"
    assert result["retries"] == 1
    assert result["missing_fields"] == []
    assert result["idempotency_key"] == "inc-demo-checkout"
    assert "__interrupt__" not in result
    assert result["timeline"][0] == "incident received"


def test_ambiguous_incident_retries_then_escalates(service: IncidentService) -> None:
    result = service.start(load_fixture("incident_ambiguous.json"))

    assert result["status"] == "needs_human"
    assert result["retries"] == 2
    assert {"service", "owner", "symptoms"} <= set(result["missing_fields"])


def test_conflicting_evidence_escalates(service: IncidentService) -> None:
    result = service.start(load_fixture("incident_conflict.json"))

    assert result["status"] == "needs_human"
    assert result["conflicts"] == ["monitoring severity conflicts with measured impact"]


def test_low_confidence_escalates_instead_of_asking_for_approval(settings: Settings) -> None:
    strict = settings.model_copy(update={"confidence_threshold": 1.1})
    service = IncidentService(strict)
    try:
        result = service.start(load_fixture("incident_complete.json"))
    finally:
        service.close()

    assert result["status"] == "needs_human"
    assert any("confidence below threshold" in entry for entry in result["timeline"])


def test_monitoring_timeout_means_unverified_impact_so_a_human_decides(
    service: IncidentService,
) -> None:
    result = service.start(load_fixture("incident_monitoring_timeout.json"))

    assert result["monitoring"]["timed_out"] is True
    assert result["status"] == "needs_human"
    assert any("monitoring timed out" in entry for entry in result["timeline"])
    assert any("confidence below threshold" in entry for entry in result["timeline"])


def test_resubmitting_a_run_returns_the_stored_run(service: IncidentService) -> None:
    payload = load_fixture("incident_duplicate_delivery.json")
    first = service.start(payload)
    second = service.start(payload)

    assert first == second
    assert len(service.search()[0]) == 1
    assert len(service.audit(payload["run_id"])) == 1
