"""WorkerPool behaviour against a real queue, with a fake handler and short real-time intervals."""

import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from incidentops.persistence.jobs import Job, JobStore, PostgresJobStore, SqliteJobStore
from incidentops.persistence.postgres import open_pool
from incidentops.persistence.repo import IncidentRepo
from incidentops.workers import WorkerPool


@pytest.fixture(params=["sqlite", "postgres"])
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[JobStore]:
    if request.param == "sqlite":
        IncidentRepo(tmp_path / "db.sqlite")
        yield SqliteJobStore(tmp_path / "db.sqlite")
        return
    pool = open_pool(request.getfixturevalue("pg_url"), max_size=10)
    yield PostgresJobStore(pool)
    pool.close()


def wait_until(condition: Callable[[], bool], timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "condition not met in time"
        time.sleep(0.02)


def status(store: JobStore, run_id: str) -> str:
    row = store.get("run", run_id)
    assert row is not None
    return str(row["status"])


@pytest.fixture
def pools() -> Iterator[list[WorkerPool]]:
    created: list[WorkerPool] = []
    yield created
    for pool in created:
        pool.stop(timeout=3)


def start(pools: list[WorkerPool], store: JobStore, handler: Any, **kwargs: Any) -> WorkerPool:
    options: dict[str, Any] = {
        "concurrency": 1,
        "poll_seconds": 0.02,
        "lease_seconds": 5.0,
        "run_timeout": 30.0,
        "retry_backoff": 0.01,
    }
    pool = WorkerPool(store, handler, **{**options, **kwargs})
    pools.append(pool)
    pool.start()
    return pool


def test_a_queued_job_is_run_and_completed(store: JobStore, pools: list[WorkerPool]) -> None:
    seen: list[Job] = []
    store.enqueue("run", "a", {"hello": "world"})

    start(pools, store, seen.append)
    wait_until(lambda: status(store, "a") == "done")

    assert [(job.run_id, job.payload, job.attempts) for job in seen] == [
        ("a", {"hello": "world"}, 1)
    ]


def test_many_jobs_across_workers_each_run_exactly_once(
    store: JobStore, pools: list[WorkerPool]
) -> None:
    seen: list[str] = []
    lock = threading.Lock()

    def handler(job: Job) -> None:
        time.sleep(0.01)
        with lock:
            seen.append(job.run_id)

    for i in range(30):
        store.enqueue("run", f"r{i}", {})
    start(pools, store, handler, concurrency=4)
    start(pools, store, handler, concurrency=4)  # a second "replica" on the same queue
    wait_until(lambda: store.stats()["done"] == 30)

    assert sorted(seen) == sorted(f"r{i}" for i in range(30))


def test_a_failing_job_is_retried_then_reported_dead(
    store: JobStore, pools: list[WorkerPool]
) -> None:
    outcomes: list[tuple[int, str, str]] = []

    def boom(job: Job) -> None:
        raise RuntimeError("model down")

    store.enqueue("run", "a", {}, max_attempts=3)
    start(
        pools,
        store,
        boom,
        on_failure=lambda job, error, outcome: outcomes.append((job.attempts, error, outcome)),
    )
    wait_until(lambda: status(store, "a") == "dead")

    assert outcomes == [
        (1, "RuntimeError: model down", "queued"),
        (2, "RuntimeError: model down", "queued"),
        (3, "RuntimeError: model down", "dead"),
    ]
    row = store.get("run", "a")
    assert row is not None and row["last_error"] == "RuntimeError: model down"


def test_a_job_that_fails_once_then_succeeds_completes(
    store: JobStore, pools: list[WorkerPool]
) -> None:
    attempts: list[int] = []

    def flaky(job: Job) -> None:
        attempts.append(job.attempts)
        if job.attempts == 1:
            raise ConnectionError("blip")

    store.enqueue("run", "a", {}, max_attempts=3)
    start(pools, store, flaky)
    wait_until(lambda: status(store, "a") == "done")

    assert attempts == [1, 2]


def test_a_hung_run_times_out_and_frees_the_worker(
    store: JobStore, pools: list[WorkerPool]
) -> None:
    release = threading.Event()
    done: list[str] = []
    errors: list[str] = []

    def handler(job: Job) -> None:
        if job.run_id == "hung":
            release.wait(20)
        else:
            done.append(job.run_id)

    store.enqueue("run", "hung", {}, max_attempts=1)
    store.enqueue("run", "next", {}, max_attempts=1)
    start(
        pools,
        store,
        handler,
        run_timeout=0.3,
        on_failure=lambda job, error, outcome: errors.append(error),
    )
    try:
        wait_until(lambda: status(store, "hung") == "dead")
        wait_until(lambda: status(store, "next") == "done")  # the single worker moved on
    finally:
        release.set()

    assert errors == ["run exceeded 0.3s timeout"] and done == ["next"]


def test_the_heartbeat_keeps_a_long_run_from_being_stolen(
    store: JobStore, pools: list[WorkerPool]
) -> None:
    runs: list[str] = []

    def slow(job: Job) -> None:
        runs.append(job.run_id)
        time.sleep(1.0)  # far longer than the 0.3s lease

    store.enqueue("run", "a", {})
    start(pools, store, slow, lease_seconds=0.3)
    start(pools, store, slow, lease_seconds=0.3)  # would grab it if the lease lapsed
    wait_until(lambda: status(store, "a") == "done")

    assert runs == ["a"]


def test_a_worker_that_loses_its_lease_abandons_the_run_without_touching_the_job(
    store: JobStore, pools: list[WorkerPool]
) -> None:
    started, release = threading.Event(), threading.Event()
    reports: list[str] = []

    def handler(job: Job) -> None:
        started.set()
        release.wait(20)

    store.enqueue("run", "a", {})
    start(
        pools,
        store,
        handler,
        lease_seconds=0.3,
        on_failure=lambda job, error, outcome: reports.append(outcome),
    )
    assert started.wait(5)
    # Another worker takes over (as it would after a long stall) and holds a valid lease.
    store._run(  # type: ignore[attr-defined]
        "UPDATE jobs SET locked_by='thief', locked_until=? WHERE kind='run' AND run_id='a'",
        (time.time() + 1000,),
    )
    time.sleep(0.6)  # at least one heartbeat has now been refused
    release.set()
    time.sleep(0.3)

    row = store.get("run", "a")
    assert row is not None
    assert (row["status"], row["locked_by"]) == ("running", "thief")  # untouched by the loser
    assert reports == []  # and the loser reported nothing


def test_an_idle_pool_stops_promptly(store: JobStore) -> None:
    pool = WorkerPool(store, lambda job: None, concurrency=3, poll_seconds=30.0)
    pool.start()

    started = time.monotonic()
    pool.stop(timeout=5)

    assert time.monotonic() - started < 2


def test_stop_does_not_hang_on_a_stuck_job(store: JobStore) -> None:
    release = threading.Event()
    started = threading.Event()
    store.enqueue("run", "a", {})

    def stuck(job: Job) -> None:
        started.set()
        release.wait(20)

    pool = WorkerPool(store, stuck, concurrency=1, poll_seconds=0.02, run_timeout=30.0)
    pool.start()
    try:
        assert started.wait(5)
        began = time.monotonic()
        pool.stop(timeout=0.5)
        assert time.monotonic() - began < 2
        assert status(store, "a") == "running"  # keeps its lease: another worker will retry it
    finally:
        release.set()
        pool.stop(timeout=5)  # let the worker finish before the fixture closes the database


def test_wake_runs_a_job_without_waiting_for_the_poll(
    store: JobStore, pools: list[WorkerPool]
) -> None:
    ran = threading.Event()
    pool = start(pools, store, lambda job: ran.set(), poll_seconds=30.0)
    time.sleep(0.2)  # the worker is now asleep for up to 30s

    store.enqueue("run", "a", {})
    pool.wake()

    assert ran.wait(3)


def test_a_vanished_worker_on_the_last_attempt_is_reported_by_the_reaper(
    store: JobStore, pools: list[WorkerPool]
) -> None:
    reaped: list[tuple[str, str]] = []
    store.enqueue("run", "a", {}, max_attempts=1)
    assert store.claim("ghost", 0.05) is not None  # leased, then the "process" dies
    time.sleep(0.15)

    start(
        pools, store, lambda job: None, on_reaped=lambda kind, run_id: reaped.append((kind, run_id))
    )
    wait_until(lambda: bool(reaped))

    assert reaped == [("run", "a")] and status(store, "a") == "dead"


def test_database_errors_do_not_kill_the_workers(store: JobStore, pools: list[WorkerPool]) -> None:
    class Flaky:
        """Proxy that fails the first two claims, as a brief database outage would."""

        def __init__(self, inner: JobStore) -> None:
            self._inner = inner
            self.failures = 2

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

        def claim(self, *args: Any, **kwargs: Any) -> Job | None:
            if self.failures:
                self.failures -= 1
                raise ConnectionError("db restarting")
            return self._inner.claim(*args, **kwargs)

    seen: list[str] = []
    store.enqueue("run", "a", {})
    start(pools, Flaky(store), lambda job: seen.append(job.run_id))  # type: ignore[arg-type]
    wait_until(lambda: status(store, "a") == "done")

    assert seen == ["a"]


def test_a_failed_completion_write_does_not_kill_the_worker(
    store: JobStore, pools: list[WorkerPool]
) -> None:
    class CompleteFailsOnce:
        """Proxy whose first ``complete`` raises, as when the database drops mid-write."""

        def __init__(self, inner: JobStore) -> None:
            self._inner = inner
            self.failed = False

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

        def complete(self, *args: Any, **kwargs: Any) -> bool:
            if not self.failed:
                self.failed = True
                raise ConnectionError("db went away")
            return self._inner.complete(*args, **kwargs)

    seen: list[str] = []
    store.enqueue("run", "a", {})
    store.enqueue("run", "b", {})
    # One worker: if it died on the first failure, the second job would never run.
    start(
        pools,
        CompleteFailsOnce(store),  # type: ignore[arg-type]
        lambda job: seen.append(job.run_id),
        concurrency=1,
    )
    wait_until(lambda: len(seen) >= 2)

    assert sorted(seen) == ["a", "b"]
