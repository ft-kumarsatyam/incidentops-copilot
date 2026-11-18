"""A pool of worker threads that lease jobs from the queue and run them.

Each job is run on its own daemon thread while the worker thread supervises it:

* a heartbeat keeps the lease alive, so a healthy long run is never stolen;
* if the lease is lost (another worker took it after a stall), the result is abandoned;
* if the run exceeds ``run_timeout`` the attempt is failed and retried with backoff.

Python cannot kill a thread, so a timed-out or abandoned run may still finish in the
background. That is safe because every write it makes is idempotent and its attempt can no
longer complete or fail the job (the queue checks ownership), and the underlying model and
HTTP calls have their own timeouts. The point of ``run_timeout`` is to free the queue slot and
surface the problem, not to guarantee the work stopped.
"""

import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

from incidentops.core.logging import get_logger
from incidentops.persistence.jobs import Job, JobStore

log = get_logger(__name__)

Handler = Callable[[Job], None]
# (job, error, outcome) where outcome is "queued" (will retry) or "dead" (gave up).
FailureHook = Callable[[Job, str, str], None]
# (kind, run_id) for jobs dead-lettered after their worker vanished.
ReapHook = Callable[[str, str], None]

_MAX_BACKOFF = 60.0


class WorkerPool:
    def __init__(
        self,
        jobs: JobStore,
        handler: Handler,
        *,
        concurrency: int = 2,
        poll_seconds: float = 1.0,
        lease_seconds: float = 60.0,
        run_timeout: float = 300.0,
        retry_backoff: float = 2.0,
        on_failure: FailureHook | None = None,
        on_reaped: ReapHook | None = None,
    ) -> None:
        self._jobs = jobs
        self._handler = handler
        self._concurrency = max(0, concurrency)
        self._poll = poll_seconds
        self._lease = lease_seconds
        self._run_timeout = run_timeout
        self._backoff = retry_backoff
        self._on_failure = on_failure
        self._on_reaped = on_reaped
        self._prefix = f"w-{uuid.uuid4().hex[:6]}"
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._threads: list[threading.Thread] = []
        self._active = 0
        self._active_lock = threading.Lock()

    # -- lifecycle -------------------------------------------------------------------------
    def start(self) -> None:
        for index in range(self._concurrency):
            thread = threading.Thread(
                target=self._loop, args=(index,), name=f"{self._prefix}-{index}", daemon=True
            )
            thread.start()
            self._threads.append(thread)
        log.info("workers.started", concurrency=self._concurrency, id=self._prefix)

    def wake(self) -> None:
        """Nudge idle workers to look for work now instead of at the next poll."""
        self._wake.set()

    def stop(self, timeout: float = 10.0) -> None:
        """Stop claiming new jobs and wait up to ``timeout`` for running ones to finish.

        A job still running after that keeps its lease, so another worker re-runs it once the
        lease lapses. Nothing is lost; it just runs again.
        """
        self._stop.set()
        self._wake.set()
        deadline = time.monotonic() + timeout
        for thread in self._threads:
            thread.join(max(0.0, deadline - time.monotonic()))
        log.info("workers.stopped", id=self._prefix)

    @property
    def active(self) -> int:
        with self._active_lock:
            return self._active

    # -- the loop --------------------------------------------------------------------------
    def _loop(self, index: int) -> None:
        worker_id = f"{self._prefix}-{index}"
        while not self._stop.is_set():
            try:
                if index == 0:
                    self._reap()
                job = self._jobs.claim(worker_id, self._lease)
            except Exception as error:  # database hiccup: back off, never die
                log.warning("workers.claim_failed", error=type(error).__name__)
                self._stop.wait(self._poll)
                continue
            if job is None:
                self._wake.wait(self._poll)
                # Never clear the flag while stopping: a worker that reaches its wait just after
                # another cleared it would miss the stop signal and sleep a whole poll interval.
                if not self._stop.is_set():
                    self._wake.clear()
                continue
            with self._active_lock:
                self._active += 1
            try:
                self._supervise(job, worker_id)
            except Exception as error:  # e.g. the database vanished while recording the outcome:
                # the lease lapses and the job is retried; this worker must not die over it
                log.error("workers.supervise_failed", run_id=job.run_id, error=type(error).__name__)
            finally:
                with self._active_lock:
                    self._active -= 1

    def _reap(self) -> None:
        for kind, run_id in self._jobs.reap_exhausted():
            log.error("workers.dead_lettered", kind=kind, run_id=run_id, reason="lease lost")
            if self._on_reaped:
                self._on_reaped(kind, run_id)

    def _supervise(self, job: Job, worker_id: str) -> None:
        outcome: dict[str, Any] = {}
        finished = threading.Event()

        def run() -> None:
            try:
                self._handler(job)
            except BaseException as error:  # the handler's failure, whatever it is
                outcome["error"] = f"{type(error).__name__}: {error}"
            finally:
                finished.set()

        threading.Thread(target=run, name=f"job-{job.run_id}", daemon=True).start()

        started = time.monotonic()
        next_beat = started + self._lease / 3
        while not finished.wait(0.05):
            now = time.monotonic()
            if now - started > self._run_timeout:
                self._fail(job, worker_id, f"run exceeded {self._run_timeout:g}s timeout")
                return
            if now >= next_beat:
                next_beat = now + self._lease / 3
                try:
                    alive = self._jobs.heartbeat(job.id, worker_id, self._lease)
                except Exception as error:
                    log.warning("workers.heartbeat_failed", error=type(error).__name__)
                    continue  # transient: the lease has slack, try again next beat
                if not alive:
                    log.warning("workers.lease_lost", run_id=job.run_id)
                    return  # someone else owns it now; abandon this attempt quietly

        if "error" in outcome:
            self._fail(job, worker_id, outcome["error"])
        elif not self._jobs.complete(job.id, worker_id):
            log.warning("workers.complete_rejected", run_id=job.run_id)  # lease was lost

    def _fail(self, job: Job, worker_id: str, error: str) -> None:
        retry_in = min(_MAX_BACKOFF, self._backoff * (2 ** (job.attempts - 1)))
        try:
            result = self._jobs.fail(job.id, worker_id, error, retry_in)
        except Exception as hook_error:
            log.error("workers.fail_not_recorded", error=type(hook_error).__name__)
            return  # the lease will lapse and the job is retried or reaped
        if result is None:
            return  # not the owner any more; the new owner reports its own outcome
        log.warning(
            "workers.job_failed",
            run_id=job.run_id,
            attempt=job.attempts,
            outcome=result,
            error=error[:200],
        )
        if self._on_failure:
            try:
                self._on_failure(job, error, result)
            except Exception as hook_error:
                log.error("workers.failure_hook_failed", error=type(hook_error).__name__)
