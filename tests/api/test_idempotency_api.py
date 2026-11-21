"""``Idempotency-Key`` on POST /incidents: a retried request returns the same run."""

import copy
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from incidentops.api.app import create_app
from incidentops.core.config import Settings
from tests.api.identity import PAYLOAD, SHARED, headers

KEY = {"Idempotency-Key": "order-1234"}


def _body(**changes: object) -> dict[str, object]:
    body = copy.deepcopy(PAYLOAD)
    body.pop("run_id", None)
    body.update(changes)
    return body


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    secured = settings.model_copy(update={"incidentops_api_key": "shared-secret"})
    with TestClient(create_app(secured)) as test_client:
        yield test_client


def test_a_retry_with_the_same_key_returns_the_same_run_once(client: TestClient) -> None:
    first = client.post("/incidents", json=_body(), headers={**SHARED, **KEY})
    second = client.post("/incidents", json=_body(), headers={**SHARED, **KEY})

    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["run_id"] == second.json()["run_id"]
    assert first.json()["run_id"].startswith("idem-")
    assert "idempotent-replayed" not in first.headers
    assert second.headers["idempotent-replayed"] == "true"
    created = [
        e
        for e in client.get(f"/incidents/{first.json()['run_id']}/audit", headers=SHARED).json()
        if e["event"] == "created"
    ]
    assert len(created) == 1  # the analysis ran once


def test_the_same_key_with_a_different_request_is_a_conflict(client: TestClient) -> None:
    client.post("/incidents", json=_body(), headers={**SHARED, **KEY})
    other = client.post(
        "/incidents", json=_body(summary="Something else entirely"), headers={**SHARED, **KEY}
    )

    assert other.status_code == 409
    assert "different request" in other.json()["detail"]


def test_different_keys_make_different_runs(client: TestClient) -> None:
    a = client.post("/incidents", json=_body(), headers={**SHARED, "Idempotency-Key": "a"})
    b = client.post("/incidents", json=_body(), headers={**SHARED, "Idempotency-Key": "b"})

    assert a.json()["run_id"] != b.json()["run_id"]


def test_a_key_and_a_run_id_together_are_refused(client: TestClient) -> None:
    response = client.post(
        "/incidents", json={**_body(), "run_id": "mine-1"}, headers={**SHARED, **KEY}
    )

    assert response.status_code == 422


@pytest.mark.parametrize("key", ["has space", "x" * 129, "tab\there"])
def test_malformed_keys_are_rejected(client: TestClient, key: str) -> None:
    response = client.post("/incidents", json=_body(), headers={**SHARED, "Idempotency-Key": key})

    assert response.status_code == 422


def test_two_tenants_can_use_the_same_key_without_seeing_each_other(client: TestClient) -> None:
    acme = headers(client, "a", ["responder"], tenant="acme")
    globex = headers(client, "g", ["responder"], tenant="globex")

    one = client.post("/incidents", json=_body(), headers={**acme, **KEY})
    two = client.post("/incidents", json=_body(), headers={**globex, **KEY})

    assert one.status_code == two.status_code == 200
    assert one.json()["run_id"] != two.json()["run_id"]
    assert client.get(f"/incidents/{one.json()['run_id']}", headers=globex).status_code == 404


def test_secrets_in_the_request_do_not_break_the_comparison(client: TestClient) -> None:
    body = _body(summary="db down, password=hunter2")
    first = client.post("/incidents", json=body, headers={**SHARED, **KEY})
    second = client.post("/incidents", json=body, headers={**SHARED, **KEY})

    assert second.status_code == 200 and first.json()["run_id"] == second.json()["run_id"]


def test_requests_without_a_key_behave_as_before(client: TestClient) -> None:
    first = client.post("/incidents", json=PAYLOAD, headers=SHARED)

    assert first.json()["run_id"] == PAYLOAD["run_id"]
    assert "idempotent-replayed" not in first.headers
