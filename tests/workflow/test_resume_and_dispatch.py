from typing import Any

import httpx
import pytest

from incidentops.core.config import Settings
from incidentops.core.errors import IncidentConflictError, IncidentNotFoundError
from incidentops.workflows.incident import IncidentService
from tests.conftest import load_fixture

WEBHOOK = "https://n8n.example/webhook/incidentops/actions"


def _service(settings: Settings, dispatcher: Any) -> IncidentService:
    configured = settings.model_copy(update={"n8n_action_webhook_url": WEBHOOK})
    return IncidentService(configured, dispatcher=dispatcher)


def test_decision_resumes_after_process_restart(settings: Settings) -> None:
    payload = load_fixture("incident_complete.json")
    first = IncidentService(settings)
    started = first.start(payload)
    first.close()

    second = IncidentService(settings)  # new process, same database file
    try:
        decided = second.decide(payload["run_id"], started["idempotency_key"], "approved")
    finally:
        second.close()

    assert decided["status"] == "succeeded"
    assert decided["action_dispatch"] == "not_configured"
    # Resuming continues the same run: earlier history is kept, not re-created.
    assert decided["timeline"][0] == "incident received"
    assert decided["timeline"].count("incident received") == 1
    assert decided["retries"] == 1
    assert "human decision: approved" in decided["timeline"]


def test_rejected_decision_cancels_without_dispatch(settings: Settings) -> None:
    calls: list[str] = []
    service = _service(settings, lambda *a, **k: calls.append("x") or {})
    try:
        payload = load_fixture("incident_complete.json")
        service.start(payload)
        result = service.decide(payload["run_id"], payload["run_id"], "rejected")
    finally:
        service.close()

    assert result["status"] == "cancelled"
    assert calls == []


def test_second_decision_is_a_conflict_and_does_not_redispatch(settings: Settings) -> None:
    calls: list[str] = []
    service = _service(settings, lambda *a, **k: calls.append("x") or {})
    try:
        payload = load_fixture("incident_complete.json")
        service.start(payload)
        service.decide(payload["run_id"], payload["run_id"], "approved")
        with pytest.raises(IncidentConflictError, match="not awaiting approval"):
            service.decide(payload["run_id"], payload["run_id"], "approved")
    finally:
        service.close()

    assert calls == ["x"]


def test_wrong_idempotency_key_is_a_conflict(service: IncidentService) -> None:
    payload = load_fixture("incident_complete.json")
    service.start(payload)

    with pytest.raises(IncidentConflictError, match="idempotency key mismatch"):
        service.decide(payload["run_id"], "wrong", "approved")


def test_unknown_run_is_not_found(service: IncidentService) -> None:
    with pytest.raises(IncidentNotFoundError):
        service.decide("nope", "nope", "approved")


def test_failed_dispatch_marks_run_failed_then_retry_succeeds(settings: Settings) -> None:
    outcomes: list[Exception | None] = [httpx.ConnectError("n8n down"), None]

    def dispatcher(*args: Any, **kwargs: Any) -> dict[str, Any]:
        outcome = outcomes.pop(0)
        if outcome:
            raise outcome
        return {"ok": True}

    service = _service(settings, dispatcher)
    try:
        payload = load_fixture("incident_complete.json")
        service.start(payload)
        failed = service.decide(payload["run_id"], payload["run_id"], "approved")
        assert failed["status"] == "failed"
        assert failed["action_dispatch"] == "failed"
        assert failed["approval"] == "approved"

        recovered = service.retry_dispatch(payload["run_id"])
        assert recovered["status"] == "succeeded"
        assert recovered["action_dispatch"] == "sent"

        events = [entry["event"] for entry in service.audit(payload["run_id"])]
        assert events == [
            "created",
            "decision:approved",
            "dispatch:failed",
            "dispatch:sent",
        ]
    finally:
        service.close()


def test_retry_dispatch_requires_a_failed_run(service: IncidentService) -> None:
    payload = load_fixture("incident_complete.json")
    service.start(payload)

    with pytest.raises(IncidentConflictError):
        service.retry_dispatch(payload["run_id"])


def test_actor_is_recorded_in_the_audit_log(service: IncidentService) -> None:
    payload = load_fixture("incident_complete.json")
    service.start(payload, actor="alice")
    service.decide(payload["run_id"], payload["run_id"], "approved", actor="bob")

    actors = [(entry["event"], entry["actor"]) for entry in service.audit(payload["run_id"])]
    assert ("created", "alice") in actors
    assert ("decision:approved", "bob") in actors


def test_service_publishes_an_event_whenever_a_run_changes(service: IncidentService) -> None:
    seen: list[dict[str, Any]] = []
    service.events.publish = seen.append  # type: ignore[method-assign]
    payload = load_fixture("incident_complete.json")

    service.start(payload)
    service.decide(payload["run_id"], payload["run_id"], "approved")

    assert seen[0] == {
        "type": "incident",
        "run_id": payload["run_id"],
        "status": "awaiting_approval",
    }
    assert seen[-1]["status"] == "succeeded"
    assert {event["run_id"] for event in seen} == {payload["run_id"]}


def test_decision_that_fails_validation_publishes_nothing(service: IncidentService) -> None:
    payload = load_fixture("incident_complete.json")
    service.start(payload)
    seen: list[dict[str, Any]] = []
    service.events.publish = seen.append  # type: ignore[method-assign]

    with pytest.raises(IncidentConflictError):
        service.decide(payload["run_id"], payload["run_id"], "approved", approved_actions=["nope"])

    assert seen == []
