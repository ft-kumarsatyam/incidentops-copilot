"""Build a worker pool for an IncidentService (used by the API and `incidentops worker`)."""

from typing import TYPE_CHECKING

from incidentops.workers.pool import WorkerPool

if TYPE_CHECKING:
    from incidentops.workflows.incident.service import IncidentService


def build_pool(service: "IncidentService", concurrency: int | None = None) -> WorkerPool:
    settings = service.settings
    return WorkerPool(
        service.jobs,
        service.handle_job,
        concurrency=settings.worker_concurrency if concurrency is None else concurrency,
        poll_seconds=settings.worker_poll_seconds,
        lease_seconds=settings.job_lease_seconds,
        run_timeout=settings.run_timeout_seconds,
        retry_backoff=settings.job_retry_backoff_seconds,
        on_failure=service.job_failed,
        on_reaped=service.job_reaped,
    )


def start_workers(service: "IncidentService") -> WorkerPool | None:
    """Start in-process workers when running async. None when there is nothing to start
    (sync mode, or WORKER_CONCURRENCY=0 because workers run in their own process)."""
    settings = service.settings
    if settings.execution_mode != "async" or settings.worker_concurrency <= 0:
        return None
    pool = build_pool(service)
    service.on_enqueue = pool.wake
    pool.start()
    return pool
