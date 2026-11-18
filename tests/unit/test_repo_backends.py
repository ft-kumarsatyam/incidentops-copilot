"""The repo contract, run against SQLite always and Postgres when TEST_DATABASE_URL is set."""

from concurrent.futures import ThreadPoolExecutor

from incidentops.persistence.base import Repo


def _state(run_id: str, status: str = "awaiting_approval", **extra: str) -> dict[str, str]:
    return {"run_id": run_id, "status": status, "summary": "s", "service": "svc", **extra}


def test_ping(repo: Repo) -> None:
    assert repo.ping() is True


def test_get_returns_none_for_unknown_runs(repo: Repo) -> None:
    assert repo.get("missing") is None


def test_state_round_trips_with_timestamps(repo: Repo) -> None:
    repo.upsert({**_state("a"), "timeline": ["x", "ünïcode ✓"], "retries": 2})

    stored = repo.get("a")

    assert stored is not None
    assert stored["timeline"] == ["x", "ünïcode ✓"] and stored["retries"] == 2
    assert stored["created_at"] and stored["updated_at"]


def test_claim_is_exclusive(repo: Repo) -> None:
    repo.upsert(_state("a"))

    assert repo.claim("a", "awaiting_approval") is True
    assert repo.claim("a", "awaiting_approval") is False
    repo.release("a", "awaiting_approval")
    assert repo.claim("a", "awaiting_approval") is True


def test_only_one_of_many_concurrent_claims_wins(repo: Repo) -> None:
    repo.upsert(_state("race"))

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: repo.claim("race", "awaiting_approval"), range(24)))

    assert results.count(True) == 1


def test_release_only_touches_busy_runs(repo: Repo) -> None:
    repo.upsert(_state("a", status="succeeded"))

    repo.release("a", "awaiting_approval")

    stored = repo.get("a")
    assert stored is not None and stored["status"] == "succeeded"


def test_list_filters_search_and_paginates(repo: Repo) -> None:
    repo.upsert(_state("a", service="checkout-api"))
    repo.upsert(_state("b", status="needs_human", service="auth-api"))
    repo.upsert(_state("c", status="succeeded", service="auth-api"))

    open_items, total = repo.search(statuses=["awaiting_approval", "needs_human"])
    assert total == 2
    assert {item["run_id"] for item in open_items} == {"a", "b"}

    _, total = repo.search(query="auth")
    assert total == 2
    page, total = repo.search(limit=1, offset=1)
    assert len(page) == 1 and total == 3


def test_search_is_case_insensitive_and_treats_input_as_data(repo: Repo) -> None:
    repo.upsert(_state("a", service="Checkout-API"))

    assert repo.search(query="checkout")[1] == 1
    assert repo.search(query="'; DROP TABLE incidents; --")[1] == 0
    assert repo.search()[1] == 1


def test_created_at_survives_updates(repo: Repo) -> None:
    repo.upsert(_state("a"))
    first = repo.get("a")
    assert first is not None
    repo.upsert(_state("a", status="succeeded"))

    updated = repo.get("a")
    assert updated is not None and updated["created_at"] == first["created_at"]
    assert updated["status"] == "succeeded"


def test_audit_is_ordered_and_scoped_to_its_run(repo: Repo) -> None:
    repo.add_audit("a", "created", "alice", {"status": "x"})
    repo.add_audit("b", "created", "bob")
    repo.add_audit("a", "decision:approved", "carol", {"approved_actions": ["one"]})

    entries = repo.audit_for("a")

    assert [entry["event"] for entry in entries] == ["created", "decision:approved"]
    assert entries[1]["actor"] == "carol"
    assert entries[1]["detail"] == {"approved_actions": ["one"]}
    assert repo.audit_for("none") == []


def test_insert_new_creates_a_run_once_and_never_overwrites(repo: Repo) -> None:
    assert repo.insert_new(_state("a", status="queued")) is True

    repo.upsert(_state("a", status="running"))  # a worker moves it on
    assert repo.insert_new(_state("a", status="queued")) is False  # a late duplicate submit

    stored = repo.get("a")
    assert stored is not None and stored["status"] == "running"  # not reset


def test_concurrent_insert_new_has_exactly_one_winner(repo: Repo) -> None:
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(lambda _: repo.insert_new(_state("race", status="queued")), range(20))
        )

    assert results.count(True) == 1


def test_a_visible_claim_changes_what_readers_see(repo: Repo) -> None:
    repo.upsert({**_state("a", status="queued"), "timeline": ["x"], "note": "keep me"})

    assert repo.claim("a", "queued", to="running", visible=True) is True

    stored = repo.get("a")
    assert stored is not None
    assert stored["status"] == "running"  # readers see it, not just the status column
    assert stored["timeline"] == ["x"] and stored["note"] == "keep me"  # the rest is untouched
    assert repo.search(statuses=["running"])[1] == 1  # and the column agrees
    assert repo.claim("a", "queued", to="running", visible=True) is False  # no longer queued


def test_the_default_claim_is_a_lock_that_readers_do_not_see(repo: Repo) -> None:
    repo.upsert(_state("a"))

    assert repo.claim("a", "awaiting_approval") is True

    stored = repo.get("a")
    assert stored is not None and stored["status"] == "awaiting_approval"  # unchanged for readers
    assert repo.search(statuses=["busy"])[1] == 1  # but excluded for other claimers
