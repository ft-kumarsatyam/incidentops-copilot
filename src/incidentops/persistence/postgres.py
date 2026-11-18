"""Postgres implementation of the incident read-model and audit log.

Same contract as ``IncidentRepo`` (SQLite). Every operation is a single statement on an
autocommit pooled connection, so ``claim`` stays one atomic UPDATE across replicas. The pool is
shared with LangGraph's ``PostgresSaver``, which needs autocommit and dict rows.
"""

import json
from typing import Any

from incidentops.persistence.migrations import migrate_postgres
from incidentops.persistence.repo import _now


def _import_psycopg() -> tuple[Any, Any, Any]:
    try:
        import psycopg
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool
    except ImportError as error:  # pragma: no cover - exercised only without the extra
        raise RuntimeError(
            "DATABASE_URL needs the Postgres extra: pip install 'incidentops[postgres]'"
        ) from error
    return psycopg, dict_row, ConnectionPool


def open_pool(url: str, *, max_size: int = 10) -> Any:
    """Create a connection pool and run migrations. Fails fast if the database is unreachable."""
    psycopg, dict_row, connection_pool = _import_psycopg()
    with psycopg.connect(url, autocommit=True) as connection:
        migrate_postgres(connection)
    pool = connection_pool(
        url,
        min_size=1,
        max_size=max_size,
        kwargs={"autocommit": True, "row_factory": dict_row},
        open=False,
    )
    pool.open(wait=True, timeout=10)
    return pool


class PostgresIncidentRepo:
    def __init__(self, pool: Any) -> None:
        self._pool = pool

    def close(self) -> None:
        self._pool.close()

    def ping(self) -> bool:
        with self._pool.connection() as connection:
            connection.execute("SELECT 1")
        return True

    def upsert(self, state: dict[str, Any]) -> None:
        now = _now()
        with self._pool.connection() as connection:
            connection.execute(
                """
                INSERT INTO incidents
                    (run_id, status, severity, service, summary, state_json, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id) DO UPDATE SET
                    status=EXCLUDED.status, severity=EXCLUDED.severity,
                    service=EXCLUDED.service, summary=EXCLUDED.summary,
                    state_json=EXCLUDED.state_json, updated_at=EXCLUDED.updated_at
                """,
                (
                    state["run_id"],
                    state.get("status", "running"),
                    state.get("severity"),
                    state.get("service"),
                    state.get("summary"),
                    json.dumps(state, sort_keys=True, default=str),
                    now,
                    now,
                ),
            )

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._pool.connection() as connection:
            row = connection.execute(
                "SELECT state_json, created_at, updated_at FROM incidents WHERE run_id = %s",
                (run_id,),
            ).fetchone()
        return self._hydrate(row) if row else None

    def search(
        self,
        *,
        statuses: list[str] | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        clauses: list[str] = []
        params: list[Any] = []
        if statuses:
            clauses.append("status = ANY(%s)")
            params.append(list(statuses))
        if query:
            # ILIKE matches SQLite's case-insensitive LIKE, so both backends search alike.
            clauses.append("(summary ILIKE %s OR service ILIKE %s OR run_id ILIKE %s)")
            like = f"%{query}%"
            params.extend([like, like, like])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._pool.connection() as connection:
            total_row = connection.execute(
                f"SELECT COUNT(*) AS total FROM incidents {where}", params
            ).fetchone()
            rows = connection.execute(
                f"SELECT state_json, created_at, updated_at FROM incidents {where} "
                "ORDER BY updated_at DESC LIMIT %s OFFSET %s",
                [*params, limit, offset],
            ).fetchall()
        return [self._hydrate(row) for row in rows], int(total_row["total"])

    def claim(self, run_id: str, expected: str) -> bool:
        """Atomically move ``expected`` -> ``busy``. Only one concurrent caller can win."""
        with self._pool.connection() as connection:
            cursor = connection.execute(
                "UPDATE incidents SET status='busy', updated_at=%s WHERE run_id=%s AND status=%s",
                (_now(), run_id, expected),
            )
            return bool(cursor.rowcount == 1)

    def release(self, run_id: str, status: str) -> None:
        with self._pool.connection() as connection:
            connection.execute(
                "UPDATE incidents SET status=%s, updated_at=%s WHERE run_id=%s AND status='busy'",
                (status, _now(), run_id),
            )

    def add_audit(
        self, run_id: str, event: str, actor: str, detail: dict[str, Any] | None = None
    ) -> None:
        with self._pool.connection() as connection:
            connection.execute(
                "INSERT INTO audit_log (run_id, event, actor, detail_json, at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (run_id, event, actor, json.dumps(detail or {}, default=str), _now()),
            )

    def audit_for(self, run_id: str) -> list[dict[str, Any]]:
        with self._pool.connection() as connection:
            rows = connection.execute(
                "SELECT event, actor, detail_json, at FROM audit_log WHERE run_id=%s ORDER BY id",
                (run_id,),
            ).fetchall()
        return [
            {
                "event": row["event"],
                "actor": row["actor"],
                "detail": json.loads(row["detail_json"]),
                "at": row["at"],
            }
            for row in rows
        ]

    @staticmethod
    def _hydrate(row: dict[str, Any]) -> dict[str, Any]:
        state: dict[str, Any] = json.loads(row["state_json"])
        state["created_at"] = row["created_at"]
        state["updated_at"] = row["updated_at"]
        return state
