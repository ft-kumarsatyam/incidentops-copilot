"""EXECUTION_MODE=async over HTTP: the 202 contract, Prefer: wait, the queue and retry endpoints."""

import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from incidentops.api.app import create_app
from incidentops.core.config import Settings
from tests.conftest import load_fixture

KEY = {"X-API-Key": "k"}
PAYLOAD = load_fixture("incident_complete.json")
RUN = PAYLOAD["run_id"]


def make_client(settings: Settings, **overrides: Any) -> TestClient:
    config = settings.model_copy(
        update={
            "incidentops_api_key": "k",
            "execution_mode": "async",
            "worker_poll_seconds": 0.02,
            "job_retry_backoff_seconds": 0.01,
            "job_max_attempts": 2,
            **overrides,
        }
    )
    return TestClient(create_app(config))


@pytest.fixture
def api(settings: Settings) -> Iterator[TestClient]:
    """Async mode with in-process workers, as a single-node deployment runs it."""
    with make_client(settings, worker_concurrency=2) as client:
        yield client


@pytest.fixture
def api_no_workers(settings: Settings) -> Iterator[TestClient]:
    """Async mode where nothing consumes the queue, so the queued state stays put."""
    with make_client(settings, worker_concurrency=0) as client:
        yield client


def wait_for_status(client: TestClient, wanted: str, timeout: float = 8.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        body: dict[str, Any] = client.get(f"/incidents/{RUN}", headers=KEY).json()
        if body["status"] == wanted:
            return body
        assert time.monotonic() < deadline, f"stuck in {body['status']}, wanted {wanted}"
        time.sleep(0.05)


def test_post_answers_202_with_a_location_and_a_queued_run(api_no_workers: TestClient) -> None:
    response = api_no_workers.post("/incidents", json=PAYLOAD, headers=KEY)

    assert response.status_code == 202
    assert response.headers["Location"] == f"/incidents/{RUN}"
    assert response.headers["Retry-After"] == "2"
    body = response.json()
    assert body["run_id"] == RUN and body["status"] == "queued"
    assert body["proposed_actions"] == []  # nothing analysed yet, and the schema still holds


def test_a_queued_run_shows_up_in_reads_and_the_incident_list(
    api_no_workers: TestClient,
) -> None:
    api_no_workers.post("/incidents", json=PAYLOAD, headers=KEY)

    assert api_no_workers.get(f"/incidents/{RUN}", headers=KEY).json()["status"] == "queued"
    listing = api_no_workers.get("/incidents?status=queued", headers=KEY)
    assert [item["run_id"] for item in listing.json()] == [RUN]


def test_resubmitting_an_in_flight_run_is_still_202_and_does_not_double_queue(
    api_no_workers: TestClient,
) -> None:
    first = api_no_workers.post("/incidents", json=PAYLOAD, headers=KEY)
    again = api_no_workers.post("/incidents", json=PAYLOAD, headers=KEY)

    assert first.status_code == again.status_code == 202
    assert api_no_workers.get("/queue", headers=KEY).json()["queued"] == 1


def test_workers_pick_the_run_up_and_it_reaches_approval(api: TestClient) -> None:
    assert api.post("/incidents", json=PAYLOAD, headers=KEY).status_code == 202

    body = wait_for_status(api, "awaiting_approval")

    assert body["severity"] and body["proposed_actions"]
    decision = {"idempotency_key": body["idempotency_key"], "decision": "approved"}
    approved = api.post(f"/incidents/{RUN}/decision", json=decision, headers=KEY)
    assert approved.status_code == 200 and approved.json()["status"] == "succeeded"


def test_prefer_wait_returns_the_finished_run_with_200(api: TestClient) -> None:
    response = api.post("/incidents", json=PAYLOAD, headers={**KEY, "Prefer": "wait=10"})

    assert response.status_code == 200
    assert response.json()["status"] == "awaiting_approval"
    assert "Location" not in response.headers


def test_prefer_wait_gives_up_with_202_when_the_run_is_not_done_in_time(
    api_no_workers: TestClient,
) -> None:
    started = time.monotonic()
    response = api_no_workers.post(
        "/incidents", json=PAYLOAD, headers={**KEY, "Prefer": "respond-async, wait=1"}
    )

    assert response.status_code == 202 and response.json()["status"] == "queued"
    assert 0.9 <= time.monotonic() - started < 5


@pytest.mark.parametrize("prefer", ["wait=abc", "wait=", "respond-async", "wait", ""])
def test_a_malformed_or_missing_wait_is_just_202(api_no_workers: TestClient, prefer: str) -> None:
    response = api_no_workers.post("/incidents", json=PAYLOAD, headers={**KEY, "Prefer": prefer})

    assert response.status_code == 202


def test_wait_is_capped_by_the_server(settings: Settings) -> None:
    with make_client(settings, worker_concurrency=0, max_wait_seconds=0.3) as client:
        started = time.monotonic()
        response = client.post("/incidents", json=PAYLOAD, headers={**KEY, "Prefer": "wait=600"})

    assert response.status_code == 202 and time.monotonic() - started < 5


def test_sync_mode_is_unchanged_and_ignores_prefer(settings: Settings) -> None:
    config = settings.model_copy(update={"incidentops_api_key": "k"})  # default: sync
    with TestClient(create_app(config)) as client:
        response = client.post("/incidents", json=PAYLOAD, headers={**KEY, "Prefer": "wait=5"})

    assert response.status_code == 200 and response.json()["status"] == "awaiting_approval"


def test_queue_endpoint_reports_depth_and_requires_auth(api_no_workers: TestClient) -> None:
    api_no_workers.post("/incidents", json=PAYLOAD, headers=KEY)

    stats = api_no_workers.get("/queue", headers=KEY).json()

    assert stats["queued"] == 1 and stats["dead"] == 0 and stats["dead_jobs"] == []
    assert stats["oldest_queued_seconds"] >= 0
    assert api_no_workers.get("/queue").status_code == 401


def test_readiness_reports_the_queue(api_no_workers: TestClient) -> None:
    api_no_workers.post("/incidents", json=PAYLOAD, headers=KEY)

    checks = api_no_workers.get("/ready").json()["checks"]

    assert checks["queue"].startswith("queued=1 running=0 dead=0")


@pytest.fixture
def breaking(api: TestClient) -> Iterator[Callable[[bool], None]]:
    """Switch the analysis between working and broken, like a provider outage."""
    graph = api.app.state.service._graph  # type: ignore[attr-defined]
    real = graph.invoke

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("provider outage")

    def toggle(is_broken: bool) -> None:
        graph.invoke = broken if is_broken else real

    yield toggle
    graph.invoke = real


def test_a_persistent_failure_ends_errored_and_an_operator_can_retry(
    api: TestClient, breaking: Callable[[bool], None]
) -> None:
    breaking(True)
    api.post("/incidents", json=PAYLOAD, headers=KEY)

    errored = wait_for_status(api, "errored")

    assert "provider outage" in errored["error"]
    queue = api.get("/queue", headers=KEY).json()
    assert queue["dead"] == 1 and queue["dead_jobs"][0]["run_id"] == RUN
    audit = [e["event"] for e in api.get(f"/incidents/{RUN}/audit", headers=KEY).json()]
    assert audit == ["queued", "run:retry", "run:errored"]

    breaking(False)  # the provider recovers
    retried = api.post(f"/incidents/{RUN}/retry-run", headers={**KEY, "X-Actor": "oncall"})
    assert retried.status_code == 200 and retried.json()["status"] in ("queued", "running")
    wait_for_status(api, "awaiting_approval")
    assert api.get("/queue", headers=KEY).json()["dead"] == 0


def test_retry_run_is_refused_unless_the_run_errored(api_no_workers: TestClient) -> None:
    api_no_workers.post("/incidents", json=PAYLOAD, headers=KEY)

    response = api_no_workers.post(f"/incidents/{RUN}/retry-run", headers=KEY)

    assert response.status_code == 409
    assert api_no_workers.post("/incidents/none/retry-run", headers=KEY).status_code == 404


def test_the_live_stream_announces_status_changes(api: TestClient) -> None:
    service = api.app.state.service  # type: ignore[attr-defined]
    seen: list[dict[str, Any]] = []
    real_publish = service.events.publish
    service.events.publish = lambda event: (seen.append(event), real_publish(event))[1]

    api.post("/incidents", json=PAYLOAD, headers=KEY)
    wait_for_status(api, "awaiting_approval")

    statuses = [event["status"] for event in seen if event["run_id"] == RUN]
    assert statuses[0] == "queued" and statuses[-1] == "awaiting_approval"
    assert "running" in statuses
