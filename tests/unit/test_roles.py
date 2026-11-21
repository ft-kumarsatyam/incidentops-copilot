"""Custom roles: the permission model, the repository (SQLite and Postgres) and key issuing."""

import pytest

from incidentops.persistence.base import Repo
from incidentops.security import Grant, Principal
from incidentops.security.apikeys import issue_key
from incidentops.security.principal import PERMISSIONS, ROLES


def _principal(*roles: str, grants: tuple[Grant, ...] = ()) -> Principal:
    return Principal("s", "a", "acme", frozenset(roles), "api-key", grants)


# -- the permission model ------------------------------------------------------------------
def test_grant_covers_every_service_when_no_pattern_is_given() -> None:
    assert Grant("incident:decide").covers("anything")


def test_grant_patterns_are_globs_and_case_sensitive() -> None:
    grant = Grant("incident:decide", ("checkout-*", "billing"))
    assert grant.covers("checkout-api") and grant.covers("billing")
    assert not grant.covers("billing-api") and not grant.covers("Checkout-api")
    assert not grant.covers("")


def test_builtin_roles_stay_cumulative_and_apply_to_every_service() -> None:
    approver = _principal("approver")
    assert approver.can("incident:decide") and approver.can_on("incident:decide", "any-service")
    assert not approver.can("keys:manage")
    assert _principal("admin").can("roles:manage")
    assert not _principal("approver").can("roles:manage")


def test_a_custom_role_gives_only_what_it_lists() -> None:
    p = _principal("payments-approver", grants=(Grant("incident:decide", ("pay-*",), "x"),))
    assert p.can("incident:decide") and not p.can("incident:read")
    assert p.can_on("incident:decide", "pay-gateway")
    assert not p.can_on("incident:decide", "checkout-api")


def test_a_custom_role_adds_to_a_builtin_one() -> None:
    p = _principal("viewer", "x", grants=(Grant("incident:decide", ("pay-*",), "x"),))
    assert p.can("incident:read") and p.can_on("incident:decide", "pay-api")
    assert not p.can_on("incident:decide", "other")


def test_an_unknown_role_name_grants_nothing() -> None:
    p = _principal("ghost")
    assert not any(p.can(name) for name in PERMISSIONS)


def test_describe_lists_builtin_roles_first_and_the_scoped_permissions() -> None:
    p = _principal(
        "zeta", "admin", "viewer", grants=(Grant("incident:decide", ("pay-*",), "zeta"),)
    )
    info = p.describe()
    assert info["roles"] == ["viewer", "admin", "zeta"]
    assert info["scoped_permissions"] == {"zeta:incident:decide": ["pay-*"]}
    assert "incident:decide_high_risk" in info["permissions"]  # type: ignore[operator]


def test_every_permission_maps_to_a_builtin_role() -> None:
    assert set(PERMISSIONS.values()) <= set(ROLES)


# -- the repository ------------------------------------------------------------------------
def _put(repo: Repo, tenant: str = "acme", name: str = "payments", **extra: object) -> dict:
    fields: dict = {
        "description": "d",
        "permissions": ["incident:decide", "incident:read"],
        "services": ["pay-*"],
        "created_by": "root",
    }
    fields.update(extra)
    return repo.role_upsert(tenant, name, **fields)


def test_a_role_round_trips(repo: Repo) -> None:
    stored = _put(repo)
    assert stored["name"] == "payments" and stored["services"] == ["pay-*"]
    [found] = repo.role_get_many("acme", ["payments", "nope"])
    assert found["permissions"] == ["incident:decide", "incident:read"]
    assert found["created_by"] == "root"


def test_upserting_replaces_the_role_and_keeps_its_creation_time(repo: Repo) -> None:
    first = _put(repo)
    second = _put(repo, permissions=["incident:read"], services=[])
    assert second["permissions"] == ["incident:read"] and second["services"] == []
    assert second["created_at"] == first["created_at"]
    assert len(repo.role_list("acme")) == 1


def test_roles_are_per_tenant(repo: Repo) -> None:
    _put(repo, "acme")
    assert repo.role_get_many("globex", ["payments"]) == []
    assert repo.role_list("globex") == []
    _put(repo, "globex", permissions=["incident:read"])
    assert repo.role_get_many("acme", ["payments"])[0]["permissions"][0] == "incident:decide"


def test_deleting_reports_whether_anything_was_removed(repo: Repo) -> None:
    _put(repo)
    assert repo.role_delete("globex", "payments") is False
    assert repo.role_delete("acme", "payments") is True
    assert repo.role_delete("acme", "payments") is False
    assert repo.role_list("acme") == []


def test_looking_up_no_names_returns_nothing(repo: Repo) -> None:
    assert repo.role_get_many("acme", []) == []


# -- key issuing ---------------------------------------------------------------------------
def test_a_key_may_carry_a_role_the_tenant_defined(repo: Repo) -> None:
    _put(repo)
    stored, _ = issue_key(
        repo, name="k", tenant="acme", roles=["viewer", "payments"], created_by="t"
    )
    assert sorted(stored["roles"]) == ["payments", "viewer"]


def test_a_key_cannot_carry_a_role_another_tenant_defined(repo: Repo) -> None:
    _put(repo, "globex")
    with pytest.raises(ValueError, match="unknown roles: payments"):
        issue_key(repo, name="k", tenant="acme", roles=["payments"], created_by="t")


def test_a_key_needs_at_least_one_role(repo: Repo) -> None:
    with pytest.raises(ValueError):
        issue_key(repo, name="k", tenant="acme", roles=[], created_by="t")
