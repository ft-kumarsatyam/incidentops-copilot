"""Circuit-breaker and Jev metrics on /metrics."""

import re
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from incidentops.api.app import create_app
from incidentops.core.config import Settings
from tests.api.identity import headers
from tests.conftest import require_module

SCRAPE = {"Authorization": "Bearer scrape-token"}


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    require_module("prometheus_client")
    enabled = settings.model_copy(
        update={
            "incidentops_api_key": "test-secret",
            "metrics_enabled": True,
            "metrics_token": "scrape-token",
            "jev_enabled": True,
            "jev_api_key": "k",
            "breaker_failure_threshold": 2,
        }
    )
    with TestClient(create_app(enabled)) as test_client:
        jev = test_client.app.state.service.jev  # type: ignore[attr-defined]
        jev._http = httpx.Client(
            base_url="https://jev.test",
            transport=httpx.MockTransport(lambda request: httpx.Response(503, json={})),
        )
        yield test_client


def _sample(text: str, metric: str, **labels: str) -> float:
    for line in text.splitlines():
        match = re.fullmatch(rf"{metric}(?:\{{(.*)\}})? (\S+)", line)
        if match and dict(re.findall(r'(\w+)="([^"]*)"', match.group(1) or "")) == labels:
            return float(match.group(2))
    raise AssertionError(f"{metric}{labels} not found in:\n{text}")


def _scrape(client: TestClient) -> str:
    return client.get("/metrics", headers=SCRAPE).text


def test_breakers_and_jev_counters_are_exported_at_zero(client: TestClient) -> None:
    text = _scrape(client)

    for name in ("dispatch", "jev"):
        assert _sample(text, "incidentops_circuit_state", name=name) == 0
    assert _sample(text, "incidentops_jev_calls_total") == 0
    assert _sample(text, "incidentops_jev_tokens_total", direction="input") == 0


def test_failing_jev_calls_show_up_and_open_its_circuit(client: TestClient) -> None:
    auth = headers(client, "r", ["responder"])
    body = {"state": "x", "questions": {"q": {"type": "noul", "instructions": "x"}}}
    for _ in range(3):
        client.post("/decisions", json=body, headers=auth)

    text = _scrape(client)
    assert _sample(text, "incidentops_jev_calls_total") == 2  # the third never left the process
    assert _sample(text, "incidentops_jev_errors_total") == 2
    assert _sample(text, "incidentops_circuit_state", name="jev") == 2
    assert _sample(text, "incidentops_circuit_opened_total", name="jev") == 1
    assert _sample(text, "incidentops_circuit_short_circuited_total", name="jev") == 1
