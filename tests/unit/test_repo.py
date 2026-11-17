from pathlib import Path

from incidentops.persistence.repo import IncidentRepo


def _state(run_id: str, status: str = "awaiting_approval", **extra: str) -> dict[str, str]:
    return {"run_id": run_id, "status": status, "summary": "s", "service": "svc", **extra}


def test_claim_is_exclusive(tmp_path: Path) -> None:
    repo = IncidentRepo(tmp_path / "db.sqlite")
    repo.upsert(_state("a"))

    assert repo.claim("a", "awaiting_approval") is True
    assert repo.claim("a", "awaiting_approval") is False
    repo.release("a", "awaiting_approval")
    assert repo.claim("a", "awaiting_approval") is True


def test_list_filters_search_and_paginates(tmp_path: Path) -> None:
    repo = IncidentRepo(tmp_path / "db.sqlite")
    repo.upsert(_state("a", service="checkout-api"))
    repo.upsert(_state("b", status="needs_human", service="auth-api"))
    repo.upsert(_state("c", status="succeeded", service="auth-api"))

    open_items, total = repo.search(statuses=["awaiting_approval", "needs_human"])
    assert total == 2
    assert {item["run_id"] for item in open_items} == {"a", "b"}

    found, total = repo.search(query="auth")
    assert total == 2
    page, total = repo.search(limit=1, offset=1)
    assert len(page) == 1 and total == 3


def test_created_at_survives_updates(tmp_path: Path) -> None:
    repo = IncidentRepo(tmp_path / "db.sqlite")
    repo.upsert(_state("a"))
    created = repo.get("a")["created_at"]  # type: ignore[index]
    repo.upsert(_state("a", status="succeeded"))

    updated = repo.get("a")
    assert updated is not None and updated["created_at"] == created
    assert updated["status"] == "succeeded"
