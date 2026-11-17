"""API-boundary edge cases: validation, auth, concurrency and abuse."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from fastapi.testclient import TestClient

from incidentops.api.app import create_app
from incidentops.core.config import Settings
from tests.conftest import load_fixture

KEY = {"X-API-Key": "test-secret"}


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    secured = settings.model_copy(update={"incidentops_api_key": "test-secret"})
    with TestClient(create_app(secured)) as test_client:
        yield test_client


def _create(client: TestClient, **overrides: Any) -> dict[str, Any]:
    payload = {**load_fixture("incident_complete.json"), **overrides}
    response = client.post("/incidents", json=payload, headers=KEY)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


# -- request validation ---------------------------------------------------------------------
@pytest.mark.parametrize(
    "overrides",
    [
        {"summary": ""},
        {"summary": "x" * 2001},
        {"reporter": ""},
        {"channel_id": "c" * 65},
        {"run_id": "has spaces"},
        {"run_id": "../../etc/passwd"},
        {"run_id": "x" * 129},
        {"symptoms": ["ok"] * 21},
        {"symptoms": ["x" * 501]},
        {"symptoms": "not-a-list"},
        {"monitoring": {f"k{i}": i for i in range(51)}},
        {"monitoring": "text"},
        {"github": ["list"]},
    ],
)
def test_invalid_incident_requests_are_rejected_with_422(
    client: TestClient, overrides: dict[str, Any]
) -> None:
    payload = {**load_fixture("incident_complete.json"), **overrides}

    assert client.post("/incidents", json=payload, headers=KEY).status_code == 422


def test_malformed_json_is_a_client_error_not_a_crash(client: TestClient) -> None:
    response = client.post(
        "/incidents", content=b"{not json", headers={**KEY, "Content-Type": "application/json"}
    )

    assert response.status_code == 422


def test_boundary_sized_fields_are_accepted(client: TestClient) -> None:
    created = _create(
        client,
        run_id="edge-max",
        summary="s" * 2000,
        symptoms=["y" * 500] * 20,
        reporter="r" * 128,
    )

    assert created["status"] in {"awaiting_approval", "needs_human"}


def test_unknown_fields_are_ignored_not_stored(client: TestClient) -> None:
    created = _create(
        client, run_id="extra-field", approval="approved", status="succeeded", evil="x"
    )

    assert created["status"] == "awaiting_approval"
    assert created["approval"] is None
    assert "evil" not in created


def test_a_caller_cannot_preapprove_by_sending_decision_fields(client: TestClient) -> None:
    created = _create(
        client, run_id="preapprove", approved_actions=["anything"], approval="approved"
    )

    assert created["approved_actions"] == [] and created["action_dispatch"] is None


# -- auth ----------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-API-Key": ""},
        {"X-API-Key": "wrong"},
        {"X-API-Key": "test-secret "},
        {"x-api-key": "TEST-SECRET"},
    ],
)
def test_every_incident_route_rejects_bad_credentials(
    client: TestClient, headers: dict[str, str]
) -> None:
    _create(client, run_id="auth-target")
    routes = [
        ("get", "/incidents"),
        ("get", "/incidents/auth-target"),
        ("get", "/incidents/auth-target/audit"),
        ("post", "/incidents/auth-target/decision"),
        ("post", "/incidents/auth-target/retry-dispatch"),
        ("get", "/incidents/stream"),
    ]
    for method, path in routes:
        response = getattr(client, method)(
            path, headers=headers, **({"json": {}} if method == "post" else {})
        )
        assert response.status_code == 401, (method, path)


def test_health_endpoints_stay_public(client: TestClient) -> None:
    assert client.get("/health").status_code == 200


# -- decisions -------------------------------------------------------------------------------
def test_decision_rejects_actions_that_were_never_proposed(client: TestClient) -> None:
    created = _create(client, run_id="subset")

    response = client.post(
        "/incidents/subset/decision",
        json={
            "idempotency_key": created["idempotency_key"],
            "decision": "approved",
            "approved_actions": ["Run DROP TABLE users"],
        },
        headers=KEY,
    )

    assert response.status_code == 409
    assert client.get("/incidents/subset", headers=KEY).json()["status"] == "awaiting_approval"


@pytest.mark.parametrize(
    "body",
    [
        {"decision": "approved"},  # missing key
        {"idempotency_key": "", "decision": "approved"},
        {"idempotency_key": "k", "decision": "maybe"},
        {"idempotency_key": "k", "decision": "APPROVED"},
        {"idempotency_key": "k", "decision": "approved", "approved_actions": "all"},
    ],
)
def test_malformed_decisions_are_422(client: TestClient, body: dict[str, Any]) -> None:
    _create(client, run_id="bad-decision")

    assert (
        client.post("/incidents/bad-decision/decision", json=body, headers=KEY).status_code == 422
    )


def test_approving_with_an_empty_action_list_is_refused(client: TestClient) -> None:
    created = _create(client, run_id="empty-approval")

    response = client.post(
        "/incidents/empty-approval/decision",
        json={
            "idempotency_key": created["idempotency_key"],
            "decision": "approved",
            "approved_actions": [],
        },
        headers=KEY,
    )

    assert response.status_code == 409


def test_a_rejected_run_cannot_be_approved_afterwards(client: TestClient) -> None:
    created = _create(client, run_id="reject-then-approve")
    key = created["idempotency_key"]
    path = "/incidents/reject-then-approve/decision"

    assert (
        client.post(
            path, json={"idempotency_key": key, "decision": "rejected"}, headers=KEY
        ).status_code
        == 200
    )
    again = client.post(path, json={"idempotency_key": key, "decision": "approved"}, headers=KEY)

    assert again.status_code == 409
    assert client.get("/incidents/reject-then-approve", headers=KEY).json()["status"] == "cancelled"


def test_needs_human_runs_cannot_be_approved_through_the_api(client: TestClient) -> None:
    created = _create(
        client, run_id="escalated", monitoring={"service": "checkout-api", "error_rate": "lots"}
    )
    assert created["status"] == "needs_human"

    response = client.post(
        "/incidents/escalated/decision",
        json={"idempotency_key": created["idempotency_key"], "decision": "approved"},
        headers=KEY,
    )

    assert response.status_code == 409


def test_concurrent_approvals_let_exactly_one_win(client: TestClient) -> None:
    created = _create(client, run_id="race")
    body = {"idempotency_key": created["idempotency_key"], "decision": "approved"}

    def decide(_: int) -> int:
        return client.post("/incidents/race/decision", json=body, headers=KEY).status_code

    with ThreadPoolExecutor(max_workers=6) as pool:
        codes = sorted(pool.map(decide, range(6)))

    assert codes.count(200) == 1
    assert codes.count(409) == 5
    audit = client.get("/incidents/race/audit", headers=KEY).json()
    assert [e["event"] for e in audit].count("decision:approved") == 1


def test_retry_dispatch_is_refused_unless_the_dispatch_failed(client: TestClient) -> None:
    _create(client, run_id="no-retry")

    assert client.post("/incidents/no-retry/retry-dispatch", headers=KEY).status_code == 409


# -- hostile content stored and returned safely ----------------------------------------------
def test_sql_metacharacters_in_search_and_filters_are_inert(client: TestClient) -> None:
    _create(client, run_id="sqli", summary="'; DROP TABLE incidents; --")

    hit = client.get("/incidents", params={"q": "'; DROP TABLE incidents; --"}, headers=KEY)
    status = client.get("/incidents", params={"status": "x' OR '1'='1"}, headers=KEY)

    assert hit.status_code == 200 and status.status_code == 200
    assert status.json() == []
    assert client.get("/incidents/sqli", headers=KEY).status_code == 200  # table still exists


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 201},
        {"limit": -1},
        {"offset": -1},
        {"limit": "abc"},
        {"q": "x" * 201},
    ],
)
def test_list_parameters_are_bounded(client: TestClient, params: dict[str, Any]) -> None:
    assert client.get("/incidents", params=params, headers=KEY).status_code == 422


def test_script_tags_are_stored_as_text_and_never_become_actions(client: TestClient) -> None:
    created = _create(client, run_id="xss", summary="<script>alert(1)</script> checkout errors")

    assert not any("<script" in text for text in created["proposed_actions"])
    assert (
        client.get("/incidents/xss", headers=KEY)
        .headers["content-type"]
        .startswith("application/json")
    )


def test_actor_header_is_sanitised_for_the_audit_log(client: TestClient) -> None:
    created = _create(client, run_id="actor")
    body = {"idempotency_key": created["idempotency_key"], "decision": "rejected"}

    client.post(
        "/incidents/actor/decision",
        json=body,
        headers={**KEY, "X-Actor": "al\x00ice\n" + "z" * 200},
    )

    actors = [entry["actor"] for entry in client.get("/incidents/actor/audit", headers=KEY).json()]
    assert actors[-1].startswith("alice") and len(actors[-1]) <= 64
    assert "\n" not in actors[-1] and "\x00" not in actors[-1]


def test_secrets_in_every_text_field_are_masked_in_the_api_response(client: TestClient) -> None:
    secret = "ghp_abcdefghijklmnopqrstuvwxyz0123"
    created = _create(
        client,
        run_id="secrets",
        summary=f"token {secret}",
        symptoms=[f"password=hunter2 {secret}"],
        monitoring={"service": "checkout-api", "error_rate": 0.12, "alert": f"key {secret}"},
        github={"repo": "acme/checkout", "recent_deploy": "abc123", "message": f"oops {secret}"},
    )

    assert secret not in str(created) and "hunter2" not in str(created)
    assert secret not in str(client.get("/incidents/secrets/audit", headers=KEY).json())


def test_resubmitting_a_run_id_with_different_content_returns_the_original(
    client: TestClient,
) -> None:
    first = _create(client, run_id="dup", summary="original")
    second = _create(client, run_id="dup", summary="attacker rewrite")

    assert second["summary"] == first["summary"] == "original"
