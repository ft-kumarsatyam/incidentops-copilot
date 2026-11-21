"""The Jev client and advisor: request shape, redaction, response validation, failure modes."""

import json
from typing import Any

import httpx
import pytest

from incidentops.agents.schemas import TriageContext
from incidentops.core.config import Settings
from incidentops.decision import JevAdvisor, JevClient, JevError


def _settings(**changes: Any) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        incidentops_env="dev",
        jev_enabled=True,
        jev_api_key="jev-test-key",
        **changes,
    )


def _answers(severity: str = "SEV-1", confidence: float = 0.9, injection: float = 0.1) -> Any:
    return {
        "model": "jev-1.13.0",
        "answers": {
            "severity": {
                "type": "choice",
                "choice": severity,
                "confidence": confidence,
                "probabilities": {severity: confidence},
            },
            "injection": {"type": "noul", "noul": injection},
        },
        "usage": {"input_tokens": 120, "output_tokens": 4},
    }


def _client(handler: Any) -> JevClient:
    return JevClient(_settings(), transport=httpx.MockTransport(handler))


def _context(**changes: Any) -> TriageContext:
    base: dict[str, Any] = {
        "summary": "Checkout returns 5xx",
        "service": "checkout-api",
        "owner": "alice@example.com",
        "reporter": "bob@example.com",
        "symptoms": ["12% error rate"],
        "monitoring": {"error_rate": 0.12},
        "github": {"recent_deploy": "abc123"},
        "runbook_hits": [],
        "missing_fields": [],
    }
    return TriageContext(**{**base, **changes})


def test_a_key_is_required() -> None:
    with pytest.raises(RuntimeError, match="JEV_API_KEY"):
        JevClient(Settings(_env_file=None, jev_enabled=True))  # type: ignore[call-arg]


def test_the_request_is_bearer_authenticated_and_uses_the_configured_model() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_answers())

    client = _client(handler)
    client.evaluate("hello", {"q": {"type": "noul", "instructions": "x"}})

    [request] = seen
    assert request.url.path == "/v1/systemone"
    assert request.headers["authorization"] == "Bearer jev-test-key"
    body = json.loads(request.content)
    assert body["model"] == "jev-latest" and body["state"] == "hello"
    assert (client.calls, client.input_tokens, client.output_tokens) == (1, 120, 4)


def test_secrets_are_redacted_before_anything_is_sent() -> None:
    seen: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content)
        return httpx.Response(200, json=_answers())

    _client(handler).evaluate(
        {"log": "password=hunter2 mail ops@example.com", "token": "abc"},
        {"q": {"type": "noul", "instructions": "x"}},
    )

    sent = seen[0].decode()
    assert "hunter2" not in sent and "ops@example.com" not in sent and '"abc"' not in sent


def test_the_advisor_keeps_names_out_and_parses_the_answers() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=_answers("SEV-2", 0.8, 0.95))

    advice = JevAdvisor(_client(handler), _settings()).advise(_context())

    assert (advice.severity, advice.severity_confidence, advice.injection) == ("SEV-2", 0.8, 0.95)
    assert advice.model == "jev-1.13.0" and advice.input_tokens == 120
    state = seen[0]["state"]
    assert state["recent_deploy"] is True and "owner" not in state and "reporter" not in state
    assert set(seen[0]["questions"]) == {"severity", "injection"}


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, json={}),
        httpx.Response(422, json={"detail": []}),
        httpx.Response(200, content=b"not json"),
        httpx.Response(200, json={"answers": "nope"}),
        httpx.Response(200, json=_answers("SEV-9")),
        httpx.Response(200, json=_answers(confidence=1.5)),
        httpx.Response(200, json={**_answers(), "answers": {"severity": {}}}),
    ],
)
def test_bad_responses_raise_jev_error_and_are_counted(response: httpx.Response) -> None:
    client = _client(lambda request: response)
    with pytest.raises(JevError):
        JevAdvisor(client, _settings()).advise(_context())
    assert client.calls == 1


def test_a_network_failure_is_a_jev_error_without_the_key_in_it() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    client = _client(handler)
    with pytest.raises(JevError) as caught:
        client.evaluate("x", {"q": {"type": "noul", "instructions": "x"}})
    assert "jev-test-key" not in str(caught.value) and client.errors == 1
