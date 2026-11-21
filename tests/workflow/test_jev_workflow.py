"""Jev as an advisor in the incident workflow: it can add a reason to look, never remove one."""

from typing import Any

import httpx

from incidentops.agents.resilient import ResilientProvider
from incidentops.core.config import Settings
from incidentops.decision import JevClient
from incidentops.workflows.incident import IncidentService
from tests.agents.helpers import ScriptedProvider, triage
from tests.conftest import load_fixture


def _run(settings: Settings, response: Any, *, severity: str = "SEV-1") -> Any:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return (
            response if isinstance(response, httpx.Response) else httpx.Response(200, json=response)
        )

    jev_settings = settings.model_copy(update={"jev_enabled": True, "jev_api_key": "k"})
    jev = JevClient(jev_settings, transport=httpx.MockTransport(handler))
    service = IncidentService(
        jev_settings,
        provider=ResilientProvider(ScriptedProvider(triage(severity=severity))),
        jev=jev,
    )
    try:
        state = service.start(load_fixture("incident_complete.json"))
    finally:
        service.close()
    state["_jev_calls"] = len(calls)
    return state


def _answers(severity: str, confidence: float, injection: float = 0.0) -> dict[str, Any]:
    return {
        "model": "jev-1.13.0",
        "answers": {
            "severity": {"type": "choice", "choice": severity, "confidence": confidence},
            "injection": {"type": "noul", "noul": injection},
        },
        "usage": {"input_tokens": 50, "output_tokens": 2},
    }


def test_agreement_leaves_the_run_untouched_and_records_the_advice(settings: Settings) -> None:
    state = _run(settings, _answers("SEV-1", 0.95))

    assert state["status"] == "awaiting_approval"
    assert state["jev"]["status"] == "ok" and state["jev"]["severity"] == "SEV-1"
    assert not any("jev" in c for c in state["conflicts"])


def test_a_confident_disagreement_sends_the_run_to_a_human(settings: Settings) -> None:
    state = _run(settings, _answers("SEV-2", 0.95))

    assert state["status"] == "needs_human"
    assert any("jev rates SEV-2" in c for c in state["conflicts"])
    assert state["_jev_calls"] == 1  # the retry reuses the advice, and still keeps the conflict
    assert state["severity"] == "SEV-1"  # Jev advises; it does not rewrite the severity


def test_a_weak_disagreement_is_ignored(settings: Settings) -> None:
    state = _run(settings, _answers("SEV-2", 0.4))

    assert state["status"] == "awaiting_approval" and state["conflicts"] == []


def test_a_suspected_injection_is_flagged(settings: Settings) -> None:
    state = _run(settings, _answers("SEV-1", 0.9, injection=0.97))

    assert "jev:injection" in state["guardrail_flags"]


def test_an_outage_of_jev_is_not_an_outage_of_triage(settings: Settings) -> None:
    state = _run(settings, httpx.Response(503, json={}))

    assert state["status"] == "awaiting_approval"
    assert state["jev"]["status"] == "unavailable"
    assert "jev advisor unavailable" in state["timeline"]


def test_a_disabled_jev_leaves_no_trace(settings: Settings) -> None:
    service = IncidentService(
        settings, provider=ResilientProvider(ScriptedProvider(triage(severity="SEV-2")))
    )
    try:
        state = service.start(load_fixture("incident_complete.json"))
    finally:
        service.close()
    assert "jev" not in state
