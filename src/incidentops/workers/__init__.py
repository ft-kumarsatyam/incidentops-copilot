"""Background execution: a pool of workers that run queued jobs."""

from incidentops.workers.pool import WorkerPool
from incidentops.workers.runtime import build_pool, start_workers

__all__ = ["WorkerPool", "build_pool", "start_workers"]
