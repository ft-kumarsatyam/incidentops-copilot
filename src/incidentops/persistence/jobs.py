"""A durable job queue on the same database as everything else (SQLite or Postgres).

Why not a broker: the incident run already needs the database to be up, jobs must survive a
crash with no extra moving part, and one claim statement gives exactly-one-worker delivery.

Lifecycle: ``queued`` -> ``running`` (leased to a worker) -> ``done``, or back to ``queued``
after a failure with backoff, or ``dead`` once ``max_attempts`` is used up. A worker that dies
simply stops renewing its lease; after the lease expires the job is claimed again, and if it
has no attempts left it is moved to ``dead`` instead (so a job that crashes its worker cannot
loop forever). One job per ``(kind, run_id)`` makes enqueueing idempotent.
"""

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from incidentops.persistence.repo import _now


@dataclass(frozen=True)
class Job:
    id: int
    kind: str
    run_id: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int


class JobStore(Protocol):
    def enqueue(
        self,
        kind: str,
        run_id: str,
        payload: dict[str, Any],
        *,
        max_attempts: int = 3,
        now: float | None = None,
    ) -> bool: ...

    def arm(
        self,
        kind: str,
        run_id: str,
        payload: dict[str, Any],
        *,
        delay: float = 0.0,
        max_attempts: int = 3,
        now: float | None = None,
    ) -> None: ...

    def finish(self, kind: str, run_id: str, now: float | None = None) -> bool: ...

    def expedite(self, kind: str, now: float | None = None) -> int: ...

    def claim(
        self, worker_id: str, lease_seconds: float, now: float | None = None
    ) -> Job | None: ...

    def heartbeat(
        self, job_id: int, worker_id: str, lease_seconds: float, now: float | None = None
    ) -> bool: ...

    def complete(self, job_id: int, worker_id: str) -> bool: ...

    def fail(
        self, job_id: int, worker_id: str, error: str, retry_in: float, now: float | None = None
    ) -> str | None: ...

    def reap_exhausted(self, now: float | None = None) -> list[tuple[str, str]]: ...

    def requeue_dead(self, kind: str, run_id: str, now: float | None = None) -> bool: ...

    def get(self, kind: str, run_id: str) -> dict[str, Any] | None: ...

    def stats(self, now: float | None = None) -> dict[str, Any]: ...

    def dead(self, limit: int = 50) -> list[dict[str, Any]]: ...

    def close(self) -> None: ...


_COLUMNS = "id, kind, run_id, payload_json, attempts, max_attempts"


def _job(row: Any) -> Job:
    return Job(
        id=int(row["id"]),
        kind=row["kind"],
        run_id=row["run_id"],
        payload=json.loads(row["payload_json"]),
        attempts=int(row["attempts"]),
        max_attempts=int(row["max_attempts"]),
    )


class _SqlJobStore:
    """The queue logic, written once. Subclasses supply the connection and SQL dialect."""

    # Appended to the candidate subquery so concurrent claimers never wait on each other.
    _SKIP_LOCKED = ""

    def _run(self, sql: str, params: tuple[Any, ...] = (), *, fetch: str = "none") -> Any:
        """Execute one statement. Returns (rows, rowcount), with rows only when fetch='all'."""
        raise NotImplementedError

    # -- producing -------------------------------------------------------------------------
    def enqueue(
        self,
        kind: str,
        run_id: str,
        payload: dict[str, Any],
        *,
        max_attempts: int = 3,
        now: float | None = None,
    ) -> bool:
        """Returns True if a job was created, False if one already exists for this run."""
        now, stamp = (time.time() if now is None else now), _now()
        _, count = self._run(
            "INSERT INTO jobs (kind, run_id, payload_json, status, max_attempts, run_after, "
            "created_at, updated_at) VALUES (?, ?, ?, 'queued', ?, ?, ?, ?) "
            "ON CONFLICT (kind, run_id) DO NOTHING",
            (kind, run_id, json.dumps(payload, default=str), max_attempts, now, stamp, stamp),
        )
        return bool(count == 1)

    def arm(
        self,
        kind: str,
        run_id: str,
        payload: dict[str, Any],
        *,
        delay: float = 0.0,
        max_attempts: int = 3,
        now: float | None = None,
    ) -> None:
        """Record an intent to do something later (the outbox): create the job, due in ``delay``
        seconds, or re-open one that is ``done`` or ``dead``. A job that is already waiting or
        running is left alone. The caller does the work itself and calls ``finish``; the job only
        survives, and is picked up by a worker, if the caller never got that far."""
        now, stamp = (time.time() if now is None else now), _now()
        self._run(
            "INSERT INTO jobs (kind, run_id, payload_json, status, max_attempts, run_after, "
            "created_at, updated_at) VALUES (?, ?, ?, 'queued', ?, ?, ?, ?) "
            "ON CONFLICT (kind, run_id) DO UPDATE SET status='queued', attempts=0, "
            "payload_json=excluded.payload_json, run_after=excluded.run_after, "
            "max_attempts=excluded.max_attempts, locked_by=NULL, locked_until=NULL, "
            "last_error=NULL, updated_at=excluded.updated_at "
            "WHERE jobs.status IN ('done', 'dead')",
            (
                kind,
                run_id,
                json.dumps(payload, default=str),
                max_attempts,
                now + delay,
                stamp,
                stamp,
            ),
        )

    def finish(self, kind: str, run_id: str, now: float | None = None) -> bool:
        """The work was done by the caller: close the job whoever (if anyone) holds it."""
        _, count = self._run(
            "UPDATE jobs SET status='done', locked_by=NULL, locked_until=NULL, updated_at=? "
            "WHERE kind=? AND run_id=? AND status IN ('queued', 'running')",
            (_now(), kind, run_id),
        )
        return bool(count == 1)

    def expedite(self, kind: str, now: float | None = None) -> int:
        """Make waiting jobs of a kind due now (at startup, whoever armed them is gone)."""
        now = time.time() if now is None else now
        _, count = self._run(
            "UPDATE jobs SET run_after=?, updated_at=? "
            "WHERE kind=? AND status='queued' AND run_after > ?",
            (now, _now(), kind, now),
        )
        return int(count)

    # -- consuming -------------------------------------------------------------------------
    def claim(self, worker_id: str, lease_seconds: float, now: float | None = None) -> Job | None:
        """Lease the next ready job, or None. Expired leases with attempts left are re-claimed."""
        now = time.time() if now is None else now
        rows, _ = self._run(
            "UPDATE jobs SET status='running', locked_by=?, locked_until=?, "
            "attempts=attempts+1, updated_at=? WHERE id = ("
            "SELECT id FROM jobs WHERE (status='queued' AND run_after <= ?) "
            "OR (status='running' AND locked_until < ? AND attempts < max_attempts) "
            f"ORDER BY run_after, id LIMIT 1{self._SKIP_LOCKED}) RETURNING {_COLUMNS}",
            (worker_id, now + lease_seconds, _now(), now, now),
            fetch="all",
        )
        return _job(rows[0]) if rows else None

    def heartbeat(
        self, job_id: int, worker_id: str, lease_seconds: float, now: float | None = None
    ) -> bool:
        """Extend the lease. False means the lease was lost: stop working on this job."""
        now = time.time() if now is None else now
        _, count = self._run(
            "UPDATE jobs SET locked_until=?, updated_at=? "
            "WHERE id=? AND locked_by=? AND status='running'",
            (now + lease_seconds, _now(), job_id, worker_id),
        )
        return bool(count == 1)

    def complete(self, job_id: int, worker_id: str) -> bool:
        _, count = self._run(
            "UPDATE jobs SET status='done', locked_by=NULL, locked_until=NULL, updated_at=? "
            "WHERE id=? AND locked_by=? AND status='running'",
            (_now(), job_id, worker_id),
        )
        return bool(count == 1)

    def fail(
        self, job_id: int, worker_id: str, error: str, retry_in: float, now: float | None = None
    ) -> str | None:
        """Record a failed attempt. Returns 'queued' (will retry), 'dead', or None if not owner."""
        now = time.time() if now is None else now
        rows, _ = self._run(
            "UPDATE jobs SET status = CASE WHEN attempts >= max_attempts THEN 'dead' "
            "ELSE 'queued' END, run_after=?, locked_by=NULL, locked_until=NULL, "
            "last_error=?, updated_at=? WHERE id=? AND locked_by=? AND status='running' "
            "RETURNING status",
            (now + retry_in, error[:2000], _now(), job_id, worker_id),
            fetch="all",
        )
        return str(rows[0]["status"]) if rows else None

    def reap_exhausted(self, now: float | None = None) -> list[tuple[str, str]]:
        """Dead-letter jobs whose worker vanished on the last allowed attempt."""
        now = time.time() if now is None else now
        rows, _ = self._run(
            "UPDATE jobs SET status='dead', locked_by=NULL, locked_until=NULL, updated_at=?, "
            "last_error='worker lost its lease on the final attempt' "
            "WHERE status='running' AND locked_until < ? AND attempts >= max_attempts "
            "RETURNING kind, run_id",
            (_now(), now),
            fetch="all",
        )
        return [(row["kind"], row["run_id"]) for row in rows]

    def requeue_dead(self, kind: str, run_id: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        _, count = self._run(
            "UPDATE jobs SET status='queued', attempts=0, run_after=?, updated_at=? "
            "WHERE kind=? AND run_id=? AND status='dead'",
            (now, _now(), kind, run_id),
        )
        return bool(count == 1)

    # -- inspecting ------------------------------------------------------------------------
    def get(self, kind: str, run_id: str) -> dict[str, Any] | None:
        rows, _ = self._run(
            "SELECT id, kind, run_id, status, attempts, max_attempts, run_after, locked_by, "
            "locked_until, last_error, created_at, updated_at FROM jobs WHERE kind=? AND run_id=?",
            (kind, run_id),
            fetch="all",
        )
        return dict(rows[0]) if rows else None

    def stats(self, now: float | None = None) -> dict[str, Any]:
        now = time.time() if now is None else now
        rows, _ = self._run("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status", fetch="all")
        counts: dict[str, Any] = {"queued": 0, "running": 0, "done": 0, "dead": 0}
        counts.update({row["status"]: int(row["n"]) for row in rows})
        oldest, _ = self._run(
            "SELECT MIN(run_after) AS oldest FROM jobs WHERE status='queued' AND run_after <= ?",
            (now,),
            fetch="all",
        )
        waiting = oldest[0]["oldest"] if oldest else None
        counts["oldest_queued_seconds"] = round(max(0.0, now - waiting), 1) if waiting else 0.0
        return counts

    def dead(self, limit: int = 50) -> list[dict[str, Any]]:
        rows, _ = self._run(
            "SELECT kind, run_id, attempts, last_error, updated_at FROM jobs "
            "WHERE status='dead' ORDER BY updated_at DESC LIMIT ?",
            (limit,),
            fetch="all",
        )
        return [dict(row) for row in rows]


class SqliteJobStore(_SqlJobStore):
    def __init__(self, database: Path) -> None:
        self.database = database

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.database, timeout=10)) as connection:
            connection.row_factory = sqlite3.Row
            with connection:
                yield connection

    def _run(self, sql: str, params: tuple[Any, ...] = (), *, fetch: str = "none") -> Any:
        with self._connect() as connection:
            cursor = connection.execute(sql, params)
            rows = cursor.fetchall() if fetch == "all" else []
            return rows, cursor.rowcount

    def close(self) -> None:
        return None


class PostgresJobStore(_SqlJobStore):
    _SKIP_LOCKED = " FOR UPDATE SKIP LOCKED"

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    def _run(self, sql: str, params: tuple[Any, ...] = (), *, fetch: str = "none") -> Any:
        with self._pool.connection() as connection:
            cursor = connection.execute(sql.replace("?", "%s"), params)
            rows = cursor.fetchall() if fetch == "all" else []
            return rows, cursor.rowcount

    def close(self) -> None:
        return None  # the pool is owned and closed by the storage that created it
