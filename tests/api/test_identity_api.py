"""Authentication, RBAC, tenant isolation, key management and audit endpoints over HTTP."""

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from incidentops.api.app import create_app
from incidentops.api.events import EventBus, event_stream
from incidentops.core.config import Settings
from incidentops.persistence.audit import SYSTEM_TENANT
from tests.api.identity import PAYLOAD, RUN, SHARED, decision, headers, payload


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    secured = settings.model_copy(update={"incidentops_api_key": "shared-secret"})
    with TestClient(create_app(secured)) as test_client:
        yield test_client


def _system_events(client: TestClient) -> list[dict[str, Any]]:
    rows, _ = client.app.state.service.repo.audit_search(tenant=SYSTEM_TENANT, limit=500)  # type: ignore[attr-defined]
    return rows


# -- authentication ------------------------------------------------------------------------
def test_requests_without_credentials_get_a_plain_401_and_are_audited(client: TestClient) -> None:
    response = client.get("/incidents")

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid credentials"}  # nothing about *why*
    assert response.headers["www-authenticate"] == "Bearer"
    [event] = _system_events(client)
    assert event["event"] == "auth:denied" and event["detail"]["reason"] == "no credentials"
    assert event["detail"]["path"] == "/incidents"


@pytest.mark.parametrize(
    "bad",
    [
        {"X-API-Key": "nope"},
        {"Authorization": "Bearer not.a.jwt"},
        {"Authorization": "Basic abc"},
        {"Authorization": "Bearer iops_000000000000_deadbeef"},
        {"X-API-Key": "iops_short"},
    ],
)
def test_bad_credentials_are_refused_without_leaking_them(
    client: TestClient, bad: dict[str, str]
) -> None:
    assert client.get("/incidents", headers=bad).status_code == 401
    assert all(value not in str(_system_events(client)) for value in bad.values())


def test_the_shared_key_still_works_as_before(client: TestClient) -> None:
    created = client.post("/incidents", json=PAYLOAD, headers={**SHARED, "X-Actor": "alice"})
    assert created.status_code == 200
    decided = client.post(
        f"/incidents/{RUN}/decision", json=decision(), headers=SHARED | {"X-Actor": "bob"}
    )
    assert decided.status_code == 200
    audit = client.get(f"/incidents/{RUN}/audit", headers=SHARED).json()
    assert [e["actor"] for e in audit] == ["alice", "bob", "bob"]


def test_health_and_ready_stay_public(client: TestClient) -> None:
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code in (200, 503)


def test_a_wrong_credential_is_refused_even_in_open_dev_mode(settings: Settings) -> None:
    with TestClient(create_app(settings)) as dev:  # no shared key, no OIDC: open
        assert dev.get("/incidents").status_code == 200
        assert dev.get("/incidents", headers={"X-API-Key": "anything"}).status_code == 401
        assert dev.get("/me").json()["method"] == "anonymous"


# -- the identity decides the actor ---------------------------------------------------------
def test_a_scoped_keys_name_is_the_actor_and_x_actor_is_ignored(client: TestClient) -> None:
    creator = headers(client, "alerting", ["responder"])
    approver = headers(client, "alice-key", ["approver"])
    client.post("/incidents", json=PAYLOAD, headers=creator | {"X-Actor": "someone-else"})
    client.post(
        f"/incidents/{RUN}/decision",
        json=decision(),
        headers=approver | {"X-Actor": "ceo@example.com"},  # a forged label changes nothing
    )

    audit = client.get(f"/incidents/{RUN}/audit", headers=approver).json()

    assert [e["actor"] for e in audit] == ["key:alerting", "key:alice-key", "key:alice-key"]


def test_me_describes_the_caller(client: TestClient) -> None:
    body = client.get("/me", headers=headers(client, "bot", ["responder"])).json()
    assert body["roles"] == ["responder"] and body["tenant"] == "acme"
    assert body["method"] == "api-key" and body["actor"] == "key:bot"
    assert "incident:create" in body["permissions"]
    assert "incident:decide" not in body["permissions"]


def test_me_requires_authentication(client: TestClient) -> None:
    assert client.get("/me").status_code == 401


# -- RBAC ----------------------------------------------------------------------------------
READ, CREATE, DECIDE, QUEUE, AUDIT, KEYS = (
    ("get", "/incidents"),
    ("post", "/incidents"),
    ("post", f"/incidents/{RUN}/decision"),
    ("get", "/queue"),
    ("get", "/audit"),
    ("get", "/admin/keys"),
)


def _call(client: TestClient, route: tuple[str, str], hdr: dict[str, str]) -> int:
    method, path = route
    body = {"json": PAYLOAD} if route == CREATE else {"json": decision()} if route == DECIDE else {}
    return client.request(method, path, headers=hdr, **body).status_code


@pytest.mark.parametrize(
    ("role", "route", "allowed"),
    [
        ("viewer", READ, True),
        ("viewer", CREATE, False),
        ("viewer", DECIDE, False),
        ("viewer", QUEUE, False),
        ("viewer", AUDIT, False),
        ("viewer", KEYS, False),
        ("responder", READ, True),
        ("responder", CREATE, True),
        ("responder", QUEUE, True),
        ("responder", DECIDE, False),
        ("responder", AUDIT, False),
        ("approver", READ, True),
        ("approver", CREATE, True),
        ("approver", DECIDE, True),
        ("approver", AUDIT, False),
        ("approver", KEYS, False),
        ("admin", DECIDE, True),
        ("admin", AUDIT, True),
        ("admin", KEYS, True),
    ],
)
def test_each_role_can_do_exactly_what_it_should(
    client: TestClient, role: str, route: tuple[str, str], allowed: bool
) -> None:
    if route == DECIDE:  # something must be waiting for a decision
        client.post("/incidents", json=PAYLOAD, headers=headers(client, "seed", ["responder"]))
    status = _call(client, route, headers(client, f"{role}-key", [role]))
    assert (status == 403) is (not allowed), status
    if allowed:
        assert status < 400


def test_a_forbidden_attempt_is_audited_against_the_callers_tenant(client: TestClient) -> None:
    viewer = headers(client, "intern", ["viewer"])
    assert client.post("/incidents", json=PAYLOAD, headers=viewer).status_code == 403
    rows, _ = client.app.state.service.repo.audit_search(tenant="acme", event="auth")  # type: ignore[attr-defined]
    assert rows[0]["event"] == "auth:forbidden" and rows[0]["actor"] == "key:intern"
    assert rows[0]["detail"]["permission"] == "incident:create"
    assert client.get(f"/incidents/{RUN}", headers=viewer).status_code == 404  # nothing was made


def test_a_principal_with_no_roles_can_do_nothing(client: TestClient) -> None:
    import incidentops.security.apikeys as apikeys

    repo = client.app.state.service.repo  # type: ignore[attr-defined]
    repo.key_insert("0" * 12, "empty", apikeys._digest("s"), "acme", [], "test", None)
    hdr = {"Authorization": f"Bearer iops_{'0' * 12}_s"}
    assert client.get("/incidents", headers=hdr).status_code == 403
    assert client.get("/me", headers=hdr).status_code == 200


# -- tenant isolation ----------------------------------------------------------------------
@pytest.fixture
def two_tenants(client: TestClient) -> dict[str, dict[str, str]]:
    return {
        "acme": headers(client, "acme-admin", ["admin"], tenant="acme"),
        "globex": headers(client, "globex-admin", ["admin"], tenant="globex"),
    }


def test_a_tenant_cannot_read_another_tenants_incident(
    client: TestClient, two_tenants: dict[str, dict[str, str]]
) -> None:
    acme, globex = two_tenants["acme"], two_tenants["globex"]
    assert client.post("/incidents", json=PAYLOAD, headers=acme).status_code == 200

    assert client.get(f"/incidents/{RUN}", headers=acme).status_code == 200
    assert client.get(f"/incidents/{RUN}", headers=globex).status_code == 404
    assert client.get(f"/incidents/{RUN}/audit", headers=globex).status_code == 404
    assert client.get("/incidents", headers=globex).json() == []
    assert client.get("/incidents?q=checkout", headers=globex).json() == []
    assert client.get("/incidents", headers=globex).headers["x-total-count"] == "0"


def test_a_tenant_cannot_act_on_another_tenants_incident(
    client: TestClient, two_tenants: dict[str, dict[str, str]]
) -> None:
    client.post("/incidents", json=PAYLOAD, headers=two_tenants["acme"])
    globex = two_tenants["globex"]

    for path, body in [
        (f"/incidents/{RUN}/decision", decision()),
        (f"/incidents/{RUN}/retry-dispatch", None),
        (f"/incidents/{RUN}/retry-run", None),
    ]:
        assert client.post(path, json=body, headers=globex).status_code == 404, path

    assert client.get(f"/incidents/{RUN}", headers=two_tenants["acme"]).json()["status"] == (
        "awaiting_approval"
    )  # untouched


def test_the_same_report_from_two_tenants_makes_two_separate_incidents(
    client: TestClient, two_tenants: dict[str, dict[str, str]]
) -> None:
    anonymous = payload_without_id()
    one = client.post("/incidents", json=anonymous, headers=two_tenants["acme"]).json()
    two = client.post("/incidents", json=anonymous, headers=two_tenants["globex"]).json()

    assert one["run_id"] != two["run_id"]
    assert [i["run_id"] for i in client.get("/incidents", headers=two_tenants["acme"]).json()] == [
        one["run_id"]
    ]
    assert (
        client.get(f"/incidents/{one['run_id']}", headers=two_tenants["globex"]).status_code == 404
    )
    # the same tenant re-posting still deduplicates
    again = client.post("/incidents", json=anonymous, headers=two_tenants["acme"]).json()
    assert again["run_id"] == one["run_id"]


def payload_without_id() -> dict[str, Any]:
    return {k: v for k, v in PAYLOAD.items() if k != "run_id"}


def test_taking_over_another_tenants_run_id_is_a_conflict_that_reveals_nothing(
    client: TestClient, two_tenants: dict[str, dict[str, str]]
) -> None:
    client.post("/incidents", json=PAYLOAD, headers=two_tenants["acme"])

    taken = client.post("/incidents", json=payload(summary="mine"), headers=two_tenants["globex"])

    assert taken.status_code == 409
    assert taken.json() == {"detail": "run_id is already in use"}
    assert client.get(f"/incidents/{RUN}", headers=two_tenants["acme"]).json()["summary"] != "mine"


def payload(**changes: Any) -> dict[str, Any]:  # noqa: F811 - local alias for readability
    return {**PAYLOAD, **changes}


def test_the_default_tenant_keeps_its_old_ids(client: TestClient) -> None:
    created = client.post("/incidents", json=payload_without_id(), headers=SHARED).json()
    assert created["tenant_id"] == "default" and created["run_id"].startswith("inc-")


def test_the_live_stream_only_carries_the_callers_tenant() -> None:
    async def scenario() -> list[str]:
        bus = EventBus()

        async def connected() -> bool:
            return False

        stream = event_stream(
            bus, connected, heartbeat_seconds=0.05, accept=lambda e: e.get("tenant") == "acme"
        )
        frames = [await anext(stream)]  # the "ready" frame
        bus.publish({"type": "incident", "run_id": "theirs", "status": "x", "tenant": "globex"})
        bus.publish({"type": "incident", "run_id": "mine", "status": "x", "tenant": "acme"})
        bus.publish({"type": "incident", "run_id": "untagged", "status": "x"})
        for _ in range(6):
            frames.append(await asyncio.wait_for(anext(stream), 2))
        await stream.aclose()
        return frames

    text = "".join(asyncio.run(scenario()))
    assert "mine" in text and "theirs" not in text and "untagged" not in text


# -- key management ------------------------------------------------------------------------
def test_an_admin_can_create_list_use_and_revoke_a_key(client: TestClient) -> None:
    admin = headers(client, "root", ["admin"])

    created = client.post(
        "/admin/keys",
        json={"name": "grafana", "roles": ["viewer"], "expires_days": 30},
        headers=admin,
    )
    assert created.status_code == 201
    token, key = created.json()["token"], created.json()["key"]
    assert token.startswith("iops_") and key["tenant"] == "acme" and key["roles"] == ["viewer"]
    assert "secret_hash" not in key and token not in str(
        client.get("/admin/keys", headers=admin).json()
    )

    use = {"Authorization": f"Bearer {token}"}
    assert client.get("/incidents", headers=use).status_code == 200
    assert client.post("/incidents", json=PAYLOAD, headers=use).status_code == 403

    assert client.delete(f"/admin/keys/{key['id']}", headers=admin).status_code == 204
    assert client.get("/incidents", headers=use).status_code == 401
    assert client.delete(f"/admin/keys/{key['id']}", headers=admin).status_code == 404

    events = [e["event"] for e in client.get("/audit?event=key", headers=admin).json()]
    assert events == ["key:created", "key:revoked"]


def test_key_validation(client: TestClient) -> None:
    admin = headers(client, "root", ["admin"])
    for body in [
        {"name": "x", "roles": []},
        {"name": "x", "roles": ["root"]},
        {"name": "", "roles": ["viewer"]},
        {"name": "bad<name>", "roles": ["viewer"]},
        {"name": "x", "roles": ["viewer"], "expires_days": 0},
    ]:
        assert client.post("/admin/keys", json=body, headers=admin).status_code == 422, body
    assert client.delete("/admin/keys/not-an-id", headers=admin).status_code == 422


def test_admins_only_manage_their_own_tenants_keys(
    client: TestClient, two_tenants: dict[str, dict[str, str]]
) -> None:
    created = client.post(
        "/admin/keys", json={"name": "k", "roles": ["viewer"]}, headers=two_tenants["acme"]
    ).json()["key"]

    assert [k["name"] for k in client.get("/admin/keys", headers=two_tenants["globex"]).json()] == [
        "globex-admin"
    ]
    assert (
        client.delete(f"/admin/keys/{created['id']}", headers=two_tenants["globex"]).status_code
        == 404
    )


# -- audit endpoints -----------------------------------------------------------------------
def test_audit_search_is_filterable_and_tenant_scoped(
    client: TestClient, two_tenants: dict[str, dict[str, str]]
) -> None:
    acme, globex = two_tenants["acme"], two_tenants["globex"]
    client.post("/incidents", json=PAYLOAD, headers=acme)
    client.post(f"/incidents/{RUN}/decision", json=decision(), headers=acme)

    everything = client.get("/audit", headers=acme)
    assert everything.headers["x-total-count"] == str(len(everything.json()))
    assert {"created", "decision:approved", "dispatch:not_configured"} <= {
        e["event"] for e in everything.json()
    }
    decisions = client.get("/audit?event=decision&actor=key:acme-admin", headers=acme).json()
    assert [e["event"] for e in decisions] == ["decision:approved"]
    assert client.get(f"/audit?run_id={RUN}&limit=1", headers=acme).json()[0]["seq"] == 1
    assert client.get("/audit", headers=globex).json() == []
    assert client.get("/audit?limit=0", headers=acme).status_code == 422


def test_the_chain_verifies_and_tampering_is_reported(client: TestClient) -> None:
    admin = headers(client, "root", ["admin"])
    client.post("/incidents", json=PAYLOAD, headers=admin)
    assert client.get("/audit/verify", headers=admin).json()["ok"] is True

    repo = client.app.state.service.repo  # type: ignore[attr-defined]
    repo._exec(
        "UPDATE audit_log SET actor = ? WHERE tenant_id = ? AND seq = ?", ("mallory", "acme", 1)
    )

    report = client.get("/audit/verify", headers=admin).json()
    assert report["ok"] is False and report["bad_seq"] == 1


def test_export_streams_ndjson_ending_with_the_chain_check(client: TestClient) -> None:
    import json

    admin = headers(client, "root", ["admin"])
    client.post("/incidents", json=PAYLOAD, headers=admin)

    response = client.get("/audit/export", headers=admin)

    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert "attachment" in response.headers["content-disposition"]
    lines = [json.loads(line) for line in response.text.splitlines()]
    assert {line["type"] for line in lines[:-1]} == {"entry"}
    assert lines[-1]["type"] == "chain" and lines[-1]["ok"] is True
    # exporting is itself recorded
    assert "audit:exported" in [e["event"] for e in client.get("/audit", headers=admin).json()]


def test_reads_are_audited_only_when_asked_to(settings: Settings) -> None:
    for enabled in (False, True):
        config = settings.model_copy(
            update={"incidentops_api_key": "shared-secret", "audit_reads": enabled}
        )
        with TestClient(create_app(config)) as app:
            admin = headers(app, "root", ["admin"], tenant="default")
            app.post("/incidents", json=PAYLOAD, headers=admin)
            app.get(f"/incidents/{RUN}", headers=admin)
            events = [e["event"] for e in app.get("/audit", headers=admin).json()]
            assert ("read" in events) is enabled
