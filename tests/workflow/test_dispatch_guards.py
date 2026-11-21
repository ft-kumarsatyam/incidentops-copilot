"""Dispatch refuses dangerous targets and fails fast while the action webhook is down."""

from typing import Any

import httpx

from incidentops.core.config import Settings
from incidentops.workflows.incident import IncidentService
from tests.conftest import load_fixture


def _service(settings: Settings, dispatcher: Any, url: str, **changes: Any) -> IncidentService:
    configured = settings.model_copy(update={"n8n_action_webhook_url": url, **changes})
    return IncidentService(configured, dispatcher=dispatcher)


def _approve(service: IncidentService, run_id: str) -> dict[str, Any]:
    return service.decide(run_id, run_id, "approved")


def test_a_metadata_url_is_refused_and_never_called(settings: Settings) -> None:
    calls: list[str] = []
    service = _service(
        settings, lambda *a, **k: calls.append("x") or {}, "http://169.254.169.254/latest/"
    )
    try:
        payload = load_fixture("incident_complete.json")
        service.start(payload)
        state = _approve(service, payload["run_id"])
    finally:
        service.close()

    assert calls == [] and state["status"] == "failed" and state["action_dispatch"] == "failed"
    assert any(e["event"] == "dispatch:failed" for e in service.audit(payload["run_id"]))


def test_private_targets_are_refused_when_configured(settings: Settings) -> None:
    calls: list[str] = []
    service = _service(
        settings,
        lambda *a, **k: calls.append("x") or {},
        "http://10.0.0.5/hook",
        outbound_block_private=True,
    )
    try:
        payload = load_fixture("incident_complete.json")
        service.start(payload)
        state = _approve(service, payload["run_id"])
    finally:
        service.close()

    assert calls == [] and state["status"] == "failed"


def test_a_down_webhook_opens_the_circuit_and_later_dispatches_fail_fast(
    settings: Settings,
) -> None:
    calls: list[int] = []

    def dispatcher(url: str, payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        calls.append(1)
        request = httpx.Request("POST", url)
        raise httpx.HTTPStatusError(
            "boom", request=request, response=httpx.Response(503, request=request)
        )

    service = _service(
        settings, dispatcher, "https://n8n.example/hook", breaker_failure_threshold=2
    )
    try:
        payload = load_fixture("incident_complete.json")
        service.start(payload)
        run_id = payload["run_id"]
        _approve(service, run_id)  # failure 1
        service.retry_dispatch(run_id, actor="t")  # failure 2: the circuit opens
        before = len(calls)
        state = service.retry_dispatch(run_id, actor="t")  # fails fast
    finally:
        service.close()

    assert len(calls) == before and state["status"] == "failed"
    assert service.dispatch_breaker.state == "open"


def test_a_rejected_webhook_token_does_not_open_the_circuit(settings: Settings) -> None:
    def dispatcher(url: str, payload: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        request = httpx.Request("POST", url)
        raise httpx.HTTPStatusError(
            "no", request=request, response=httpx.Response(401, request=request)
        )

    service = _service(
        settings, dispatcher, "https://n8n.example/hook", breaker_failure_threshold=2
    )
    try:
        payload = load_fixture("incident_complete.json")
        service.start(payload)
        _approve(service, payload["run_id"])
        service.retry_dispatch(payload["run_id"], actor="t")
        service.retry_dispatch(payload["run_id"], actor="t")
    finally:
        service.close()

    assert service.dispatch_breaker.state == "closed"
