"""A crash between approving and sending must not lose the send."""

from pathlib import Path
from typing import Any

import httpx
import pytest

from incidentops.core.config import Settings
from incidentops.workflows.incident import IncidentService
from incidentops.workflows.incident.service import DISPATCH_JOB
from tests.conftest import load_fixture

WEBHOOK = "https://n8n.example/hook"


class Crash(BaseException):
    """Stands in for the process dying: not an Exception, so nothing in the service catches it."""


def _service(settings: Settings, dispatcher: Any, **changes: Any) -> IncidentService:
    configured = settings.model_copy(update={"n8n_action_webhook_url": WEBHOOK, **changes})
    return IncidentService(configured, dispatcher=dispatcher)


def _job(service: IncidentService, run_id: str) -> dict[str, Any] | None:
    return service.jobs.get(DISPATCH_JOB, run_id)


def _approve_and_crash(settings: Settings) -> str:
    payload = load_fixture("incident_complete.json")
    first = _service(settings, lambda *a, **k: (_ for _ in ()).throw(Crash()))
    try:
        first.start(payload)
        with pytest.raises(Crash):
            first.decide(payload["run_id"], payload["run_id"], "approved")
    finally:
        first.close()
    return str(payload["run_id"])


def test_a_normal_approval_sends_once_and_leaves_no_pending_job(settings: Settings) -> None:
    sent: list[str] = []
    service = _service(settings, lambda url, p, **k: sent.append(url) or {})
    try:
        payload = load_fixture("incident_complete.json")
        service.start(payload)
        state = service.decide(payload["run_id"], payload["run_id"], "approved")
        job = _job(service, payload["run_id"])
    finally:
        service.close()

    assert state["action_dispatch"] == "sent" and sent == [WEBHOOK]
    assert job is not None and job["status"] == "done"


def test_a_crash_after_approval_leaves_the_intent_recorded(settings: Settings) -> None:
    run_id = _approve_and_crash(settings)

    second = _service(settings, lambda *a, **k: {})
    try:
        job = _job(second, run_id)
        state = second.get(run_id)
    finally:
        second.close()

    assert job is not None and job["status"] == "queued"
    assert state["approval"] == "approved" and "action_dispatch" not in state  # the silent gap


def test_the_next_start_sends_what_the_crash_left_unsent(settings: Settings) -> None:
    run_id = _approve_and_crash(settings)
    sent: list[str] = []

    second = _service(settings, lambda url, p, **k: sent.append(url) or {})
    try:
        ran = second.recover_dispatches(drain=True)
        state = second.get(run_id)
        job = _job(second, run_id)
        audit = [e["event"] for e in second.audit(run_id)]
    finally:
        second.close()

    assert ran == 1 and sent == [WEBHOOK]
    assert state["action_dispatch"] == "sent" and state["status"] == "succeeded"
    assert job is not None and job["status"] == "done"
    assert "dispatch:recovered" in audit and "dispatch:sent" in audit


def test_recovery_does_nothing_when_the_send_actually_finished(settings: Settings) -> None:
    sent: list[str] = []
    service = _service(settings, lambda url, p, **k: sent.append(url) or {})
    try:
        payload = load_fixture("incident_complete.json")
        service.start(payload)
        service.decide(payload["run_id"], payload["run_id"], "approved")
        # Simulate the crash happening after the send but before the job was closed.
        service.jobs.arm(DISPATCH_JOB, payload["run_id"], {"actor": "api"}, delay=0)
        ran = service.recover_dispatches(drain=True)
    finally:
        service.close()

    assert ran == 1 and sent == [WEBHOOK]  # not sent a second time


def test_a_failing_recovery_is_retried_and_audited(settings: Settings) -> None:
    run_id = _approve_and_crash(settings)

    def failing(url: str, payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        request = httpx.Request("POST", url)
        raise httpx.HTTPStatusError(
            "boom", request=request, response=httpx.Response(503, request=request)
        )

    second = _service(settings, failing)
    try:
        second.recover_dispatches(drain=True)
        job = _job(second, run_id)
        audit = [e["event"] for e in second.audit(run_id)]
        state = second.get(run_id)
    finally:
        second.close()

    assert job is not None and job["status"] == "queued" and job["attempts"] == 1
    assert "dispatch:retry" in audit and state["status"] == "failed"


def test_startup_makes_a_job_that_is_still_in_its_grace_period_due(settings: Settings) -> None:
    service = _service(settings, lambda *a, **k: {})
    try:
        service.jobs.arm(DISPATCH_JOB, "later", {}, delay=3600)
        assert service.jobs.claim("w", 60) is None  # not due: its request may still be running
        assert service.recover_dispatches(drain=False) == 0
        due = service.jobs.claim("w", 60)
    finally:
        service.close()

    assert due is not None and due.kind == DISPATCH_JOB and due.run_id == "later"


def test_no_job_is_recorded_when_there_is_no_webhook(settings: Settings) -> None:
    service = IncidentService(settings)
    try:
        payload = load_fixture("incident_complete.json")
        service.start(payload)
        service.decide(payload["run_id"], payload["run_id"], "approved")
        job = _job(service, payload["run_id"])
    finally:
        service.close()

    assert job is None


def test_the_outbox_can_be_turned_off(settings: Settings) -> None:
    service = _service(settings, lambda *a, **k: {}, dispatch_outbox=False)
    try:
        payload = load_fixture("incident_complete.json")
        service.start(payload)
        service.decide(payload["run_id"], payload["run_id"], "approved")
        job = _job(service, payload["run_id"])
    finally:
        service.close()

    assert job is None


def test_a_rejection_records_nothing(settings: Settings, tmp_path: Path) -> None:
    service = _service(settings, lambda *a, **k: {})
    try:
        payload = load_fixture("incident_complete.json")
        service.start(payload)
        service.decide(payload["run_id"], payload["run_id"], "rejected")
        job = _job(service, payload["run_id"])
    finally:
        service.close()

    assert job is None
