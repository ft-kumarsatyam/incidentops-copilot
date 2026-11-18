"""Tenant scoping in the repository (SQLite and Postgres) and the approvals table."""

from incidentops.persistence.base import Repo


def _state(run_id: str, **extra: str) -> dict[str, str]:
    return {
        "run_id": run_id,
        "status": "awaiting_approval",
        "summary": "s",
        "service": "svc",
        **extra,
    }


def test_rows_default_to_the_default_tenant(repo: Repo) -> None:
    repo.upsert(_state("a"))
    assert repo.tenant_of("a") == "default"
    assert (repo.get("a") or {})["tenant_id"] == "default"


def test_a_tenant_only_sees_its_own_runs(repo: Repo) -> None:
    repo.upsert(_state("a"), tenant="acme")
    repo.upsert(_state("b"), tenant="globex")

    assert repo.get("a", tenant="acme") is not None
    assert repo.get("a", tenant="globex") is None
    assert repo.get("a") is not None  # unscoped: internal callers such as workers
    assert [s["run_id"] for s in repo.search(tenant="acme")[0]] == ["a"]
    assert repo.search(tenant="globex")[1] == 1
    assert repo.search(tenant="nobody") == ([], 0)
    assert repo.search()[1] == 2


def test_search_filters_combine_with_the_tenant(repo: Repo) -> None:
    repo.upsert(_state("a", summary="checkout is down"), tenant="acme")
    repo.upsert(_state("b", summary="checkout is down"), tenant="globex")
    items, total = repo.search(query="checkout", statuses=["awaiting_approval"], tenant="acme")
    assert total == 1 and items[0]["run_id"] == "a"


def test_a_run_never_changes_tenant(repo: Repo) -> None:
    repo.upsert(_state("a"), tenant="acme")
    repo.upsert({**_state("a"), "status": "succeeded"}, tenant="globex")  # e.g. a later sync
    assert repo.tenant_of("a") == "acme"
    assert (repo.get("a") or {})["status"] == "succeeded"


def test_insert_new_does_not_take_over_another_tenants_run(repo: Repo) -> None:
    assert repo.insert_new(_state("a"), tenant="acme") is True
    assert repo.insert_new(_state("a"), tenant="globex") is False
    assert repo.tenant_of("a") == "acme"


def test_tenant_of_unknown_run_is_none(repo: Repo) -> None:
    assert repo.tenant_of("missing") is None


def test_the_per_run_audit_trail_respects_the_tenant(repo: Repo) -> None:
    repo.upsert(_state("a"), tenant="acme")
    repo.add_audit("a", "created", "api")
    assert len(repo.audit_for("a", tenant="acme")) == 1
    assert repo.audit_for("a", tenant="globex") == []


def test_approvals_are_recorded_once_per_approver(repo: Repo) -> None:
    assert repo.add_approval("a", "alice", ["x", "y"]) == ["alice"]
    assert repo.add_approval("a", "alice", ["x", "y"]) == ["alice"]  # idempotent
    assert repo.add_approval("a", "bob", ["x", "y"]) == ["alice", "bob"]
    assert [r["approver"] for r in repo.approvals_for("a")] == ["alice", "bob"]
    assert repo.approvals_for("a")[0]["actions"] == ["x", "y"]
    assert repo.approvals_for("other") == []


def test_an_approvals_display_name_is_kept_and_defaults_to_the_id(repo: Repo) -> None:
    repo.add_approval("a", "key-1", ["x"], actor="key:alice")
    repo.add_approval("a", "key-2", ["x"])
    assert [r["actor"] for r in repo.approvals_for("a")] == ["key:alice", "key-2"]
