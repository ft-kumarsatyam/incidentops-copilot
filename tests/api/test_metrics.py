"""Prometheus metrics: off by default, fail-closed outside dev, and counting the right things."""

import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from incidentops.api.app import create_app
from incidentops.core.config import Settings
from incidentops.core.metrics import Metrics
from tests.conftest import load_fixture, require_module

KEY = {"X-API-Key": "test-secret"}
TOKEN = {"Authorization": "Bearer scrape-token"}


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    require_module("prometheus_client")
    enabled = settings.model_copy(
        update={
            "incidentops_api_key": "test-secret",
            "metrics_enabled": True,
            "metrics_token": "scrape-token",
        }
    )
    with TestClient(create_app(enabled)) as test_client:
        yield test_client


def _value(text: str, name: str, **labels: str) -> float:
    """Read one sample from the exposition text (0.0 when it has not been recorded)."""
    for line in text.splitlines():
        match = re.fullmatch(rf"{name}(?:\{{(.*)\}})? (\S+)", line)
        if match is None:
            continue
        found = dict(re.findall(r'(\w+)="([^"]*)"', match.group(1) or ""))
        if found == labels:
            return float(match.group(2))
    return 0.0


def test_metrics_are_not_exposed_unless_enabled(settings: Settings) -> None:
    with TestClient(create_app(settings.model_copy(update={"incidentops_api_key": "k"}))) as c:
        assert c.get("/metrics").status_code == 404


def test_metrics_need_the_token_when_one_is_set(client: TestClient) -> None:
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/metrics", headers=KEY).status_code == 401  # the API key is not the token
    response = client.get("/metrics", headers=TOKEN)
    assert response.status_code == 200
    assert "incidentops_build_info" in response.text


def test_startup_refuses_open_metrics_outside_dev(settings: Settings) -> None:
    require_module("prometheus_client")
    prod = settings.model_copy(
        update={
            "incidentops_env": "prod",
            "incidentops_api_key": "k",
            "metrics_enabled": True,
            "metrics_token": None,
        }
    )
    with pytest.raises(RuntimeError, match="METRICS_TOKEN"):
        create_app(prod)


def test_incident_decision_and_dispatch_are_counted(client: TestClient) -> None:
    incident = client.post(
        "/incidents", json=load_fixture("incident_complete.json"), headers=KEY
    ).json()
    client.post(
        f"/incidents/{incident['run_id']}/decision",
        json={"idempotency_key": incident["run_id"], "decision": "approved"},
        headers=KEY,
    )
    text = client.get("/metrics", headers=TOKEN).text
    assert (
        _value(text, "incidentops_incidents_total", status="awaiting_approval", severity="SEV-1")
        == 1
    )
    assert _value(text, "incidentops_decisions_total", decision="approved") == 1
    # The test settings have no action webhook, so the dispatch is recorded as not configured.
    assert _value(text, "incidentops_dispatch_total", result="not_configured") == 1


def test_a_duplicate_submission_is_not_counted_twice(client: TestClient) -> None:
    payload = load_fixture("incident_complete.json")
    client.post("/incidents", json=payload, headers=KEY)
    client.post("/incidents", json=payload, headers=KEY)
    text = client.get("/metrics", headers=TOKEN).text
    assert (
        sum(
            float(line.rsplit(" ", 1)[1])
            for line in text.splitlines()
            if line.startswith("incidentops_incidents_total{")
        )
        == 1
    )


def test_routes_are_labelled_by_template_not_by_incident_id(client: TestClient) -> None:
    client.get("/incidents/inc-does-not-exist", headers=KEY)
    client.get("/incidents/another-unknown-one", headers=KEY)
    text = client.get("/metrics", headers=TOKEN).text
    assert 'route="/incidents/{run_id}"' in text
    assert "inc-does-not-exist" not in text


def test_the_rate_limiter_is_counted(settings: Settings) -> None:
    require_module("prometheus_client")
    limited = settings.model_copy(
        update={
            "incidentops_api_key": "k",
            "rate_limit_per_minute": 1,
            "metrics_enabled": True,
            "metrics_token": "scrape-token",
        }
    )
    with TestClient(create_app(limited)) as c:
        c.get("/incidents", headers={"X-API-Key": "k"})
        assert c.get("/incidents", headers={"X-API-Key": "k"}).status_code == 429
        text = c.get("/metrics", headers=TOKEN).text  # /metrics itself is never rate limited
    assert _value(text, "incidentops_rate_limited_total") == 1


def test_disabled_metrics_do_nothing() -> None:
    off = Metrics(False)
    off.http("GET", "/x", 200, 0.1)
    off.incident_created("a", "b")
    off.decision("approved")
    off.dispatch("sent")
    off.rate_limited()
    assert off.render() == b""
