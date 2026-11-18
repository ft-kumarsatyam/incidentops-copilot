"""Asynchronous execution at the service level: queueing, retries, dead-lettering, recovery.

``drain`` runs ready jobs through the same claim / handle / fail / complete steps a real worker
uses, one at a time, so every outcome is deterministic and nothing sleeps."""

import threading
from collections.abc import Iterator
from typing import Any

import pytest

from incidentops.core.config import Settings
from incidentops.core.errors import IncidentConflictError
from incidentops.workflows.incident import IncidentService
from tests.conftest import load_fixture


@pytest.fixture
def async_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"execution_mode": "async", "worker_concurrency": 0, "job_max_attempts": 3}
    )


@pytest.fixture
def service(async_settings: Settings) -> Iterator[IncidentService]:
    instance = IncidentService(async_settings)
    yield instance
    instance.close()


def drain(service: IncidentService, *, retry_in: float = 0.0) -> int:
    """Run every ready job once, like a worker would. Returns how many were attempted."""
    attempted = 0
    while (job := service.jobs.claim("test-worker", 60)) is not None:
        attempted += 1
        try:
            service.handle_job(job)
        except Exception as error:
            outcome = service.jobs.fail(job.id, "test-worker", str(error), retry_in)
            assert outcome is not None
            service.job_failed(job, str(error), outcome)
        else:
            service.jobs.complete(job.id, "test-worker")
    return attempted


def events(service: IncidentService) -> list[dict[str, Any]]:
    return [entry for entry in service.audit(PAYLOAD["run_id"])]


PAYLOAD = load_fixture("incident_complete.json")
RUN = PAYLOAD["run_id"]


def audit_events(service: IncidentService) -> list[str]:
    return [entry["event"] for entry in service.audit(RUN)]


def test_submit_returns_immediately_with_a_queued_run_and_does_no_analysis(
    service: IncidentService,
) -> None:
    state = service.submit(PAYLOAD)

    assert state["status"] == "queued"
    assert state["timeline"] == ["queued for analysis"]
    assert "severity" not in state or state["severity"] is None  # nothing analysed yet
    assert service.queue_stats()["queued"] == 1
    assert audit_events(service) == ["queued"]


def test_create_follows_the_execution_mode(settings: Settings, service: IncidentService) -> None:
    assert service.create(PAYLOAD)["status"] == "queued"  # async service

    other_db = settings.model_copy(
        update={"database_path": settings.database_path.with_name("s.db")}
    )
    sync_service = IncidentService(other_db)  # the default mode is sync
    try:
        assert sync_service.create(PAYLOAD)["status"] == "awaiting_approval"
    finally:
        sync_service.close()


def test_resubmitting_a_run_returns_it_without_a_second_job(service: IncidentService) -> None:
    first = service.submit(PAYLOAD)
    again = service.submit(PAYLOAD)

    assert again["run_id"] == first["run_id"] and again["status"] == "queued"
    assert service.queue_stats()["queued"] == 1
    assert audit_events(service) == ["queued"]


def test_concurrent_submits_of_one_run_create_one_job_and_do_not_reset_it(
    service: IncidentService,
) -> None:
    barrier = threading.Barrier(8)
    states: list[dict[str, Any]] = []

    def submit() -> None:
        barrier.wait()
        states.append(service.submit(PAYLOAD))

    threads = [threading.Thread(target=submit) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(states) == 8 and {s["run_id"] for s in states} == {RUN}
    stats = service.queue_stats()
    assert stats["queued"] + stats["running"] == 1
    assert audit_events(service) == ["queued"]


def test_a_worker_runs_the_analysis_and_the_run_reaches_approval(
    service: IncidentService,
) -> None:
    service.submit(PAYLOAD, actor="alice")

    assert drain(service) == 1

    state = service.get(RUN)
    assert state["status"] == "awaiting_approval"
    assert state["severity"] and state["proposed_actions"]
    assert audit_events(service) == ["queued", "created"]
    assert [e["actor"] for e in service.audit(RUN)] == ["alice", "alice"]  # the submitter
    assert service.queue_stats()["done"] == 1


def test_the_result_matches_what_synchronous_execution_produces(
    settings: Settings, service: IncidentService
) -> None:
    service.submit(PAYLOAD)
    drain(service)
    queued_result = service.get(RUN)

    sync_service = IncidentService(
        settings.model_copy(update={"database_path": settings.database_path.with_name("s.db")})
    )
    try:
        sync_result = sync_service.start(PAYLOAD)
    finally:
        sync_service.close()

    for key in ("status", "severity", "confidence", "proposed_actions", "hypotheses"):
        assert queued_result[key] == sync_result[key], key


def test_the_run_is_visibly_running_while_a_worker_has_it(service: IncidentService) -> None:
    service.submit(PAYLOAD)
    seen: list[str] = []
    real_invoke = service._graph.invoke

    def spy(*args: Any, **kwargs: Any) -> Any:
        seen.append(service.get(RUN)["status"])  # what the UI would show mid-analysis
        return real_invoke(*args, **kwargs)

    service._graph.invoke = spy  # type: ignore[method-assign]
    drain(service)

    assert seen == ["running"]


def test_an_approval_after_async_analysis_works_like_any_other(service: IncidentService) -> None:
    service.submit(PAYLOAD)
    drain(service)

    decided = service.decide(RUN, RUN, "approved")

    assert decided["status"] == "succeeded"
    assert audit_events(service) == [
        "queued",
        "created",
        "decision:approved",
        "dispatch:not_configured",
    ]


def test_a_failed_attempt_is_retried_and_then_succeeds(
    service: IncidentService, monkeypatch: pytest.MonkeyPatch
) -> None:
    service.submit(PAYLOAD)
    real_invoke = service._graph.invoke
    calls: list[int] = []

    def flaky(*args: Any, **kwargs: Any) -> Any:
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("model gateway reset")
        return real_invoke(*args, **kwargs)

    monkeypatch.setattr(service._graph, "invoke", flaky)

    drain(service, retry_in=3600)  # attempt 1 fails; the retry is an hour away (backoff)
    assert service.get(RUN)["status"] == "queued"
    assert audit_events(service) == ["queued", "run:retry"]
    retry = service.audit(RUN)[1]["detail"]
    assert retry["attempt"] == 1 and "model gateway reset" in retry["error"]

    assert drain(service) == 0  # still backing off: nothing is ready
    service.jobs._run("UPDATE jobs SET run_after=0")  # type: ignore[attr-defined]
    drain(service)  # attempt 2 succeeds
    assert service.get(RUN)["status"] == "awaiting_approval"
    assert audit_events(service) == ["queued", "run:retry", "created"]


def test_a_run_that_keeps_failing_ends_errored_with_the_reason(
    service: IncidentService, monkeypatch: pytest.MonkeyPatch
) -> None:
    service.submit(PAYLOAD)

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(service._graph, "invoke", broken)

    assert drain(service) == 3  # max_attempts

    state = service.get(RUN)
    assert state["status"] == "errored"
    assert "provider exploded" in state["error"]
    assert state["timeline"][-1].startswith("analysis failed: ")
    assert audit_events(service) == ["queued", "run:retry", "run:retry", "run:errored"]
    assert service.queue_stats()["dead"] == 1
    assert service.queue_stats()["dead_jobs"][0]["run_id"] == RUN


def test_an_errored_run_can_be_retried_by_an_operator(
    service: IncidentService, monkeypatch: pytest.MonkeyPatch
) -> None:
    service.submit(PAYLOAD)
    real_invoke = service._graph.invoke
    monkeypatch.setattr(
        service._graph, "invoke", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
    )
    drain(service)
    assert service.get(RUN)["status"] == "errored"

    monkeypatch.setattr(service._graph, "invoke", real_invoke)  # the outage is over
    requeued = service.retry_run(RUN, actor="oncall")
    assert requeued["status"] == "queued"
    drain(service)

    assert service.get(RUN)["status"] == "awaiting_approval"
    assert "run:requeued" in audit_events(service)
    requeue = [e for e in service.audit(RUN) if e["event"] == "run:requeued"][0]
    assert requeue["actor"] == "oncall"


def test_only_an_errored_run_can_be_retried(service: IncidentService) -> None:
    service.submit(PAYLOAD)

    with pytest.raises(IncidentConflictError, match="failed analysis"):
        service.retry_run(RUN)
    drain(service)
    with pytest.raises(IncidentConflictError, match="failed analysis"):
        service.retry_run(RUN)  # awaiting approval is not an error


def test_rerunning_a_finished_analysis_does_not_redo_it_or_double_log(
    service: IncidentService,
) -> None:
    """A worker that finished the analysis but died before completing the job: the next attempt
    must notice the run is settled rather than analyse it again."""
    service.submit(PAYLOAD)
    job = service.jobs.claim("w1", 0.0)  # lease that is already expired
    assert job is not None
    service.handle_job(job)  # analysis done...
    # ...but the worker "died" before complete(): the lease lapses and the job is re-claimed.
    invocations: list[int] = []
    real_invoke = service._graph.invoke

    def counting(*args: Any, **kwargs: Any) -> Any:
        invocations.append(1)
        return real_invoke(*args, **kwargs)

    service._graph.invoke = counting  # type: ignore[method-assign]
    again = service.jobs.claim("w2", 60)
    assert again is not None and again.attempts == 2
    service.handle_job(again)
    service.jobs.complete(again.id, "w2")

    assert invocations == []  # nothing was re-run
    assert audit_events(service).count("created") == 1
    assert service.get(RUN)["status"] == "awaiting_approval"


def test_a_vanished_worker_on_the_last_attempt_leaves_an_errored_run(
    service: IncidentService,
) -> None:
    service.submit(PAYLOAD)
    for attempt in range(3):  # three attempts, each leased and then abandoned
        assert service.jobs.claim(f"ghost{attempt}", 0.0) is not None

    for kind, run_id in service.jobs.reap_exhausted():
        service.job_reaped(kind, run_id)

    state = service.get(RUN)
    assert state["status"] == "errored" and "stopped responding" in state["error"]
    assert audit_events(service)[-1] == "run:errored"


def test_wait_for_returns_as_soon_as_the_run_settles(service: IncidentService) -> None:
    service.submit(PAYLOAD)
    worker = threading.Timer(0.3, lambda: drain(service))
    worker.start()

    state = service.wait_for(RUN, 10)

    worker.join()
    assert state["status"] == "awaiting_approval"


def test_wait_for_gives_up_at_the_deadline(service: IncidentService) -> None:
    service.submit(PAYLOAD)  # no worker running

    assert service.wait_for(RUN, 0.3)["status"] == "queued"


def test_unknown_job_kinds_fail_loudly_instead_of_being_ignored(service: IncidentService) -> None:
    service.jobs.enqueue("mystery", "x", {})
    job = service.jobs.claim("w", 60)
    assert job is not None

    with pytest.raises(ValueError, match="unknown job kind"):
        service.handle_job(job)


def test_a_full_async_lifecycle_on_postgres(settings: Settings, pg_url: str) -> None:
    configured = settings.model_copy(
        update={"execution_mode": "async", "worker_concurrency": 0, "database_url": pg_url}
    )
    first, second = IncidentService(configured), IncidentService(configured)
    try:
        queued = first.submit(PAYLOAD)  # API replica 1 accepts it
        assert queued["status"] == "queued"
        assert second.get(RUN)["status"] == "queued"  # replica 2 sees it

        assert drain(second) == 1  # a worker on replica 2 runs it
        assert first.get(RUN)["status"] == "awaiting_approval"
        assert first.decide(RUN, RUN, "approved")["status"] == "succeeded"
    finally:
        first.close()
        second.close()
