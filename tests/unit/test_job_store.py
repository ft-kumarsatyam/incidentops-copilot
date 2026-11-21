"""The job queue contract, on SQLite always and Postgres when TEST_DATABASE_URL is set.

Time is passed in explicitly, so leases, backoff and expiry are exact and nothing sleeps."""

import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from incidentops.persistence.jobs import JobStore, PostgresJobStore, SqliteJobStore
from incidentops.persistence.postgres import open_pool
from incidentops.persistence.repo import IncidentRepo

T0 = 1_000_000.0
LEASE = 60.0


@pytest.fixture(params=["sqlite", "postgres"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[JobStore]:
    if request.param == "sqlite":
        IncidentRepo(tmp_path / "db.sqlite")  # creates the schema
        yield SqliteJobStore(tmp_path / "db.sqlite")
        return
    pool = open_pool(request.getfixturevalue("pg_url"), max_size=10)
    yield PostgresJobStore(pool)
    pool.close()


def _enqueue(store: JobStore, run_id: str, **kwargs: int) -> None:
    assert store.enqueue("run", run_id, {"run_id": run_id, "n": 1}, now=T0, **kwargs)


def test_enqueue_is_idempotent_per_run(store: JobStore) -> None:
    assert store.enqueue("run", "a", {"x": 1}, now=T0) is True
    assert store.enqueue("run", "a", {"x": 2}, now=T0) is False  # same kind and run: ignored
    assert store.enqueue("other", "a", {"x": 3}, now=T0) is True  # different kind: its own job

    job = store.claim("w1", LEASE, now=T0 + 1)
    assert job is not None and job.payload == {"x": 1}


def test_claim_returns_none_when_nothing_is_ready(store: JobStore) -> None:
    assert store.claim("w1", LEASE, now=T0) is None


def test_jobs_are_claimed_oldest_first_and_only_once(store: JobStore) -> None:
    for run_id in ("a", "b", "c"):
        _enqueue(store, run_id)

    first = store.claim("w1", LEASE, now=T0 + 1)
    second = store.claim("w2", LEASE, now=T0 + 1)
    third = store.claim("w3", LEASE, now=T0 + 1)

    assert [first.run_id, second.run_id, third.run_id] == ["a", "b", "c"]  # type: ignore[union-attr]
    assert store.claim("w4", LEASE, now=T0 + 1) is None


def test_concurrent_workers_never_claim_the_same_job(store: JobStore) -> None:
    for i in range(40):
        _enqueue(store, f"r{i}")
    claimed: list[str] = []
    lock = threading.Lock()

    def worker(name: str) -> None:
        while (job := store.claim(name, LEASE, now=T0 + 1)) is not None:
            with lock:
                claimed.append(job.run_id)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(worker, [f"w{i}" for i in range(8)]))

    assert sorted(claimed) == sorted(f"r{i}" for i in range(40))  # each exactly once


def test_a_running_job_is_not_stolen_until_its_lease_expires(store: JobStore) -> None:
    _enqueue(store, "a")
    job = store.claim("w1", LEASE, now=T0)
    assert job is not None and job.attempts == 1

    assert store.claim("w2", LEASE, now=T0 + LEASE - 1) is None  # still leased
    reclaimed = store.claim("w2", LEASE, now=T0 + LEASE + 1)  # lease lapsed: w1 is presumed dead

    assert reclaimed is not None and reclaimed.run_id == "a" and reclaimed.attempts == 2


def test_heartbeat_keeps_a_job_and_only_for_its_owner(store: JobStore) -> None:
    _enqueue(store, "a")
    job = store.claim("w1", LEASE, now=T0)
    assert job is not None

    assert store.heartbeat(job.id, "w1", LEASE, now=T0 + 50) is True
    assert store.heartbeat(job.id, "intruder", LEASE, now=T0 + 50) is False
    assert store.claim("w2", LEASE, now=T0 + 90) is None  # extended to T0+110


def test_a_lost_lease_is_visible_to_the_old_worker(store: JobStore) -> None:
    _enqueue(store, "a")
    old = store.claim("w1", LEASE, now=T0)
    assert old is not None
    new = store.claim("w2", LEASE, now=T0 + LEASE + 1)
    assert new is not None

    assert store.heartbeat(old.id, "w1", LEASE, now=T0 + LEASE + 2) is False
    assert store.complete(old.id, "w1") is False  # the stale worker cannot finish it
    assert store.fail(old.id, "w1", "late", 0) is None
    assert store.complete(new.id, "w2") is True


def test_complete_removes_the_job_from_circulation(store: JobStore) -> None:
    _enqueue(store, "a")
    job = store.claim("w1", LEASE, now=T0)
    assert job is not None

    assert store.complete(job.id, "w1") is True
    assert store.complete(job.id, "w1") is False
    assert store.claim("w2", LEASE, now=T0 + 10_000) is None
    row = store.get("run", "a")
    assert row is not None and row["status"] == "done"


def test_failure_retries_after_the_backoff_then_goes_dead(store: JobStore) -> None:
    _enqueue(store, "a", max_attempts=2)

    first = store.claim("w1", LEASE, now=T0)
    assert first is not None
    assert store.fail(first.id, "w1", "boom 1", retry_in=30, now=T0) == "queued"
    assert store.claim("w1", LEASE, now=T0 + 29) is None  # backing off
    second = store.claim("w1", LEASE, now=T0 + 31)
    assert second is not None and second.attempts == 2

    assert store.fail(second.id, "w1", "boom 2", retry_in=30, now=T0 + 31) == "dead"
    assert store.claim("w1", LEASE, now=T0 + 10_000) is None
    row = store.get("run", "a")
    assert row is not None and row["status"] == "dead" and row["last_error"] == "boom 2"


def test_a_job_that_keeps_killing_its_worker_is_dead_lettered(store: JobStore) -> None:
    _enqueue(store, "a", max_attempts=2)
    store.claim("w1", LEASE, now=T0)  # worker dies, never reports
    store.claim("w2", LEASE, now=T0 + 100)  # second and last attempt, dies too

    assert store.claim("w3", LEASE, now=T0 + 200) is None  # no attempts left: not re-run
    assert store.reap_exhausted(now=T0 + 200) == [("run", "a")]
    assert store.reap_exhausted(now=T0 + 200) == []  # reported once
    row = store.get("run", "a")
    assert row is not None and row["status"] == "dead"
    assert "lease" in row["last_error"]


def test_reaping_leaves_healthy_and_unexpired_jobs_alone(store: JobStore) -> None:
    _enqueue(store, "a", max_attempts=1)
    store.claim("w1", LEASE, now=T0)

    assert store.reap_exhausted(now=T0 + 10) == []  # lease still valid


def test_requeue_gives_a_dead_job_a_fresh_start(store: JobStore) -> None:
    _enqueue(store, "a", max_attempts=1)
    job = store.claim("w1", LEASE, now=T0)
    assert job is not None
    store.fail(job.id, "w1", "boom", retry_in=0, now=T0)

    assert store.requeue_dead("run", "missing") is False
    assert store.requeue_dead("run", "a", now=T0 + 5) is True
    assert store.requeue_dead("run", "a", now=T0 + 5) is False  # no longer dead
    again = store.claim("w2", LEASE, now=T0 + 6)
    assert again is not None and again.attempts == 1


def test_stats_and_dead_letter_listing(store: JobStore) -> None:
    for run_id in ("a", "b", "c", "d"):
        _enqueue(store, run_id, max_attempts=1)
    done = store.claim("w", LEASE, now=T0)  # a
    store.complete(done.id, "w")  # type: ignore[union-attr]
    dead = store.claim("w", LEASE, now=T0)  # b
    store.fail(dead.id, "w", "kaput", retry_in=0, now=T0)  # type: ignore[union-attr]
    store.claim("w", LEASE, now=T0)  # c is running; d stays queued

    stats = store.stats(now=T0 + 40)

    assert (stats["queued"], stats["running"], stats["done"], stats["dead"]) == (1, 1, 1, 1)
    assert stats["oldest_queued_seconds"] == 40.0  # d has waited since T0
    listing = store.dead()
    assert [(row["run_id"], row["last_error"]) for row in listing] == [("b", "kaput")]


def test_stats_on_an_empty_queue(store: JobStore) -> None:
    assert store.stats(now=T0) == {
        "queued": 0,
        "running": 0,
        "done": 0,
        "dead": 0,
        "oldest_queued_seconds": 0.0,
    }


# -- the outbox: arm, finish, expedite ------------------------------------------------------
def test_arm_creates_a_delayed_job_that_only_becomes_claimable_when_due(store: JobStore) -> None:
    store.arm("dispatch", "a", {"actor": "x"}, delay=60, now=T0)

    assert store.claim("w1", LEASE, now=T0 + 30) is None  # the caller is still doing the work
    job = store.claim("w1", LEASE, now=T0 + 61)
    assert job is not None and job.kind == "dispatch" and job.payload == {"actor": "x"}


def test_arm_leaves_a_waiting_or_running_job_alone(store: JobStore) -> None:
    store.arm("dispatch", "a", {"n": 1}, delay=60, now=T0)
    store.arm("dispatch", "a", {"n": 2}, delay=0, now=T0)  # already armed: ignored

    assert store.claim("w1", LEASE, now=T0 + 1) is None
    job = store.claim("w1", LEASE, now=T0 + 61)
    assert job is not None and job.payload == {"n": 1}
    store.arm("dispatch", "a", {"n": 3}, delay=0, now=T0 + 62)  # running: still ignored
    assert store.get("dispatch", "a")["status"] == "running"  # type: ignore[index]


def test_finish_closes_a_job_whoever_holds_it(store: JobStore) -> None:
    store.arm("dispatch", "a", {}, delay=60, now=T0)

    assert store.finish("dispatch", "a") is True
    assert store.finish("dispatch", "a") is False  # already done
    assert store.finish("dispatch", "missing") is False
    assert store.claim("w1", LEASE, now=T0 + 120) is None


def test_arm_reopens_a_finished_or_dead_job(store: JobStore) -> None:
    store.arm("dispatch", "a", {"n": 1}, delay=0, now=T0)
    store.finish("dispatch", "a")
    store.arm("dispatch", "a", {"n": 2}, delay=0, now=T0 + 1)  # e.g. a second retry-dispatch

    job = store.claim("w1", LEASE, now=T0 + 2)
    assert job is not None and job.payload == {"n": 2} and job.attempts == 1

    for tick in range(2):  # run out of attempts to reach `dead`
        store.fail(job.id, "w1", "boom", 0, now=T0 + 3 + tick)
        job = store.claim("w1", LEASE, now=T0 + 10 + tick) or job
    store.fail(job.id, "w1", "boom", 0, now=T0 + 20)
    assert store.get("dispatch", "a")["status"] == "dead"  # type: ignore[index]
    store.arm("dispatch", "a", {"n": 3}, delay=0, now=T0 + 30)
    reopened = store.claim("w2", LEASE, now=T0 + 31)
    assert reopened is not None and reopened.payload == {"n": 3} and reopened.attempts == 1


def test_expedite_makes_only_waiting_jobs_of_that_kind_due(store: JobStore) -> None:
    store.arm("dispatch", "a", {}, delay=600, now=T0)
    store.arm("dispatch", "b", {}, delay=600, now=T0)
    store.arm("other", "c", {}, delay=600, now=T0)
    store.finish("dispatch", "b")

    assert store.expedite("dispatch", now=T0 + 1) == 1
    job = store.claim("w1", LEASE, now=T0 + 2)
    assert job is not None and (job.kind, job.run_id) == ("dispatch", "a")
    assert store.claim("w1", LEASE, now=T0 + 3) is None  # "other" is still in the future
