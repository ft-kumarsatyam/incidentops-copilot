"""Custom roles and ABAC through the real API: managing roles, using them, scoping approvals."""

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from incidentops.api.app import create_app
from incidentops.core.config import Settings
from tests.api.identity import PAYLOAD, RUN, decision, headers, payload

DECIDER = {"permissions": ["incident:read", "incident:decide"], "services": ["pay-*"]}


def _client(settings: Settings, **updates: Any) -> TestClient:
    config = settings.model_copy(update={"incidentops_api_key": "shared-secret", **updates})
    return TestClient(create_app(config))


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with _client(settings) as c:
        yield c


def _define(client: TestClient, name: str, body: dict[str, Any], who: str = "root") -> Any:
    return client.put(f"/admin/roles/{name}", json=body, headers=headers(client, who, ["admin"]))


def _open(client: TestClient, **changes: Any) -> None:
    body = payload(**changes)
    response = client.post("/incidents", json=body, headers=headers(client, "o", ["responder"]))
    assert response.status_code == 200, response.text


# -- managing roles ------------------------------------------------------------------------
def test_an_admin_creates_lists_updates_and_deletes_a_role(client: TestClient) -> None:
    admin = headers(client, "root", ["admin"])
    created = client.put("/admin/roles/payments", json=DECIDER, headers=admin)
    assert created.status_code == 200
    assert created.json()["services"] == ["pay-*"] and created.json()["created_by"] == "key:root"

    listing = client.get("/admin/roles", headers=admin).json()
    assert [r["name"] for r in listing["custom"]] == ["payments"]
    assert [r["name"] for r in listing["built_in"]] == ["viewer", "responder", "approver", "admin"]
    assert listing["permissions"]["roles:manage"] == "admin"

    changed = client.put("/admin/roles/payments", json={**DECIDER, "services": []}, headers=admin)
    assert changed.json()["services"] == []

    assert client.delete("/admin/roles/payments", headers=admin).status_code == 204
    assert client.delete("/admin/roles/payments", headers=admin).status_code == 404
    events = [e["event"] for e in client.get("/audit", headers=admin).json()]
    assert {"role:created", "role:updated", "role:deleted"} <= set(events)


@pytest.mark.parametrize("role", ["viewer", "responder", "approver"])
def test_only_an_admin_manages_roles(client: TestClient, role: str) -> None:
    other = headers(client, "u", [role])
    assert client.get("/admin/roles", headers=other).status_code == 403
    assert client.put("/admin/roles/x", json=DECIDER, headers=other).status_code == 403
    assert client.delete("/admin/roles/x", headers=other).status_code == 403


def test_a_custom_role_can_be_given_roles_manage_to_delegate_administration(
    client: TestClient,
) -> None:
    _define(client, "role-keeper", {"permissions": ["roles:manage"]})
    keeper = headers(client, "keeper", ["role-keeper"])
    assert client.get("/admin/roles", headers=keeper).status_code == 200
    assert client.get("/admin/keys", headers=keeper).status_code == 403  # nothing else


@pytest.mark.parametrize(
    "name, body, fragment",
    [
        ("approver", DECIDER, "built-in"),
        ("x", {"permissions": ["incident:fly"]}, "unknown permission"),
        ("x", {"permissions": []}, ""),
        ("x", {"permissions": ["incident:read"], "services": ["a b"]}, ""),
        ("x", {"permissions": ["incident:read"], "services": ["../etc"]}, ""),
        ("bad name!", DECIDER, ""),
    ],
)
def test_invalid_roles_are_refused(
    client: TestClient, name: str, body: dict[str, Any], fragment: str
) -> None:
    response = _define(client, name, body)
    assert response.status_code in (404, 422), response.text
    assert fragment in response.text


def test_roles_belong_to_one_tenant(client: TestClient) -> None:
    _define(client, "payments", DECIDER)
    other_admin = headers(client, "g", ["admin"], tenant="globex")
    assert client.get("/admin/roles", headers=other_admin).json()["custom"] == []
    assert client.delete("/admin/roles/payments", headers=other_admin).status_code == 404


# -- using roles ---------------------------------------------------------------------------
def test_a_key_with_a_custom_role_gets_its_permissions_and_me_shows_them(
    client: TestClient,
) -> None:
    _define(client, "payments", DECIDER)
    me = client.get("/me", headers=headers(client, "p", ["payments"])).json()
    assert me["roles"] == ["payments"]
    assert "incident:decide" in me["permissions"] and "incident:create" not in me["permissions"]
    assert me["scoped_permissions"]["payments:incident:decide"] == ["pay-*"]


def test_creating_a_key_validates_custom_role_names(client: TestClient) -> None:
    _define(client, "payments", DECIDER)
    admin = headers(client, "root", ["admin"])
    ok = client.post("/admin/keys", json={"name": "n", "roles": ["payments"]}, headers=admin)
    assert ok.status_code == 201
    bad = client.post("/admin/keys", json={"name": "n", "roles": ["nope"]}, headers=admin)
    assert bad.status_code == 422 and "unknown roles" in bad.text


def test_deleting_a_role_takes_effect_on_the_next_request(client: TestClient) -> None:
    _define(client, "payments", DECIDER)
    holder = headers(client, "p", ["payments"])
    assert client.get("/incidents", headers=holder).status_code == 200
    client.delete("/admin/roles/payments", headers=headers(client, "root", ["admin"]))
    assert client.get("/incidents", headers=holder).status_code == 403


def test_changing_a_role_takes_effect_on_the_next_request(client: TestClient) -> None:
    _define(client, "payments", DECIDER)
    holder = headers(client, "p", ["payments"])
    assert client.get("/incidents", headers=holder).status_code == 200
    _define(client, "payments", {"permissions": ["queue:read"]})
    assert client.get("/incidents", headers=holder).status_code == 403


# -- ABAC: a role limited to some services -------------------------------------------------
def test_a_scoped_approver_decides_only_incidents_of_its_services(client: TestClient) -> None:
    _define(client, "payments", DECIDER)
    holder = headers(client, "p", ["payments"])
    _open(client)  # checkout-api: outside the role's scope

    refused = client.post(f"/incidents/{RUN}/decision", json=decision(), headers=holder)
    assert refused.status_code == 403 and "checkout-api" in refused.text
    forbidden = [
        e
        for e in client.get(f"/audit?run_id={RUN}", headers=headers(client, "r", ["admin"])).json()
        if e["event"] == "auth:forbidden"
    ]
    assert forbidden and forbidden[0]["actor"] == "key:p"
    assert client.get(f"/incidents/{RUN}", headers=holder).status_code == 200  # can still read


def test_a_scoped_approver_decides_incidents_inside_its_scope(client: TestClient) -> None:
    _define(client, "payments", {**DECIDER, "services": ["checkout-*"]})
    _open(client)
    ok = client.post(
        f"/incidents/{RUN}/decision", json=decision(), headers=headers(client, "p", ["payments"])
    )
    assert ok.status_code == 200 and ok.json()["status"] == "succeeded"


def test_a_scope_also_limits_rejections(client: TestClient) -> None:
    _define(client, "payments", DECIDER)
    _open(client)
    refused = client.post(
        f"/incidents/{RUN}/decision",
        json=decision(approved=False),
        headers=headers(client, "p", ["payments"]),
    )
    assert refused.status_code == 403


def test_builtin_approvers_are_not_limited_by_scope(client: TestClient) -> None:
    _open(client)
    ok = client.post(
        f"/incidents/{RUN}/decision", json=decision(), headers=headers(client, "a", ["approver"])
    )
    assert ok.status_code == 200


# -- ABAC: high-risk actions ---------------------------------------------------------------
@pytest.fixture
def risky(settings: Settings) -> Iterator[TestClient]:
    every = [
        "rollback",
        "scale",
        "page",
        "notify",
        "assign_commander",
        "follow_runbook",
        "open_issue",
    ]
    with _client(settings, high_risk_actions=every) as c:
        yield c


def test_high_risk_actions_need_the_extra_permission(risky: TestClient) -> None:
    _define(risky, "junior", {"permissions": ["incident:read", "incident:decide"]})
    _open(risky)
    refused = risky.post(
        f"/incidents/{RUN}/decision", json=decision(), headers=headers(risky, "j", ["junior"])
    )
    assert refused.status_code == 403 and "incident:decide_high_risk" in refused.text


def test_a_role_with_the_permission_may_approve_high_risk_actions(risky: TestClient) -> None:
    _define(
        risky,
        "senior",
        {"permissions": ["incident:read", "incident:decide", "incident:decide_high_risk"]},
    )
    _open(risky)
    ok = risky.post(
        f"/incidents/{RUN}/decision", json=decision(), headers=headers(risky, "s", ["senior"])
    )
    assert ok.status_code == 200


def test_rejecting_never_needs_the_high_risk_permission(risky: TestClient) -> None:
    _define(risky, "junior", {"permissions": ["incident:read", "incident:decide"]})
    _open(risky)
    ok = risky.post(
        f"/incidents/{RUN}/decision",
        json=decision(approved=False),
        headers=headers(risky, "j", ["junior"]),
    )
    assert ok.status_code == 200


def test_approving_only_low_risk_actions_does_not_need_it(settings: Settings) -> None:
    with _client(settings, high_risk_actions=["rollback", "scale"]) as c:
        _define(c, "junior", {"permissions": ["incident:read", "incident:decide"]})
        _open(c)
        items = c.get(f"/incidents/{RUN}", headers=headers(c, "v", ["viewer"])).json()
        safe = [
            i["description"]
            for i in items["action_items"]
            if i["type"] not in ("rollback", "scale")
        ]
        assert safe, "fixture proposes at least one low-risk action"
        ok = c.post(
            f"/incidents/{RUN}/decision",
            json=decision(approved_actions=safe),
            headers=headers(c, "j", ["junior"]),
        )
        assert ok.status_code == 200


def test_the_default_has_no_high_risk_actions(client: TestClient) -> None:
    _define(client, "junior", {"permissions": ["incident:read", "incident:decide"]})
    _open(client)
    ok = client.post(
        f"/incidents/{RUN}/decision", json=decision(), headers=headers(client, "j", ["junior"])
    )
    assert ok.status_code == 200


def test_the_shared_key_is_not_subject_to_scope_or_risk_rules(settings: Settings) -> None:
    with _client(settings, high_risk_actions=["rollback", "scale", "page", "notify"]) as c:
        c.post("/incidents", json=PAYLOAD, headers={"X-API-Key": "shared-secret"})
        ok = c.post(
            f"/incidents/{RUN}/decision",
            json=decision(),
            headers={"X-API-Key": "shared-secret", "X-Actor": "bob"},
        )
        assert ok.status_code == 200
