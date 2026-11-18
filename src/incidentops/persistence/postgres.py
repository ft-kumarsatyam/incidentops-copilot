"""Postgres implementation of the incident read-model and audit log.

Same contract as ``IncidentRepo`` (SQLite). Every operation is a single statement on an
autocommit pooled connection, so ``claim`` stays one atomic UPDATE across replicas; the audit
append is the one transaction (a row lock on the tenant's chain head). The pool is shared with
LangGraph's ``PostgresSaver``, which needs autocommit and dict rows.
"""

import json
from collections.abc import Mapping
from typing import Any

from incidentops.persistence.audit import (
    DEFAULT_TENANT,
    ApprovalQueries,
    AuditQueries,
    canonical,
    entry_hash,
)
from incidentops.persistence.keys import KeyQueries
from incidentops.persistence.migrations import migrate_postgres
from incidentops.persistence.repo import _now

_STATE_COLUMNS = "state_json, created_at, updated_at, tenant_id"


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


class PostgresIncidentRepo(AuditQueries, ApprovalQueries, KeyQueries):
    def __init__(self, pool: Any) -> None:
        self._pool = pool

    def close(self) -> None:
        self._pool.close()

    def ping(self) -> bool:
        with self._pool.connection() as connection:
            connection.execute("SELECT 1")
        return True

    # -- AuditQueries plumbing -------------------------------------------------------------
    def _rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[Mapping[str, Any]]:
        with self._pool.connection() as connection:
            return list(connection.execute(sql.replace("?", "%s"), params).fetchall())

    def _exec(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        with self._pool.connection() as connection:
            return int(connection.execute(sql.replace("?", "%s"), params).rowcount)

    # -- incidents -------------------------------------------------------------------------
    @staticmethod
    def _values(state: dict[str, Any], now: str) -> tuple[Any, ...]:
        return (
            state["run_id"],
            state.get("status", "running"),
            state.get("severity"),
            state.get("service"),
            state.get("summary"),
            json.dumps(state, sort_keys=True, default=str),
            now,
            now,
        )

    def upsert(self, state: dict[str, Any], tenant: str = DEFAULT_TENANT) -> None:
        """Insert or update a run. ``tenant`` applies to a new row only: a run never moves."""
        with self._pool.connection() as connection:
            connection.execute(
                """
                INSERT INTO incidents
                    (run_id, status, severity, service, summary, state_json, created_at,
                     updated_at, tenant_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id) DO UPDATE SET
                    status=EXCLUDED.status, severity=EXCLUDED.severity,
                    service=EXCLUDED.service, summary=EXCLUDED.summary,
                    state_json=EXCLUDED.state_json, updated_at=EXCLUDED.updated_at
                """,
                (*self._values(state, _now()), tenant),
            )

    def get(self, run_id: str, *, tenant: str | None = None) -> dict[str, Any] | None:
        sql = f"SELECT {_STATE_COLUMNS} FROM incidents WHERE run_id = %s"
        params: list[Any] = [run_id]
        if tenant is not None:
            sql += " AND tenant_id = %s"
            params.append(tenant)
        with self._pool.connection() as connection:
            row = connection.execute(sql, params).fetchone()
        return self._hydrate(row) if row else None

    def tenant_of(self, run_id: str) -> str | None:
        """Which tenant owns this run, or None if it does not exist."""
        rows = self._rows("SELECT tenant_id FROM incidents WHERE run_id = ?", (run_id,))
        return str(rows[0]["tenant_id"]) if rows else None

    def search(
        self,
        *,
        statuses: list[str] | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
        tenant: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        clauses: list[str] = []
        params: list[Any] = []
        if tenant is not None:
            clauses.append("tenant_id = %s")
            params.append(tenant)
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
                f"SELECT {_STATE_COLUMNS} FROM incidents {where} "
                "ORDER BY updated_at DESC LIMIT %s OFFSET %s",
                [*params, limit, offset],
            ).fetchall()
        return [self._hydrate(row) for row in rows], int(total_row["total"])

    def insert_new(self, state: dict[str, Any], tenant: str = DEFAULT_TENANT) -> bool:
        """Create the run only if it does not exist. False means someone else created it."""
        with self._pool.connection() as connection:
            cursor = connection.execute(
                "INSERT INTO incidents (run_id, status, severity, service, summary, state_json, "
                "created_at, updated_at, tenant_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (run_id) DO NOTHING",
                (*self._values(state, _now()), tenant),
            )
            return bool(cursor.rowcount == 1)

    def claim(self, run_id: str, expected: str, to: str = "busy", *, visible: bool = False) -> bool:
        """Atomically move ``expected`` -> ``to``. Only one concurrent caller can win.

        Reads come from the stored state, so by default only the status column changes (the
        lock use: `busy` is never shown). With ``visible`` the stored state is patched in the
        same statement, so readers see the new status immediately.
        """
        sql = (
            "UPDATE incidents SET status=%s, "
            "state_json=jsonb_set(state_json::jsonb, '{status}', to_jsonb(%s::text))::text, "
            "updated_at=%s WHERE run_id=%s AND status=%s"
            if visible
            else "UPDATE incidents SET status=%s, updated_at=%s WHERE run_id=%s AND status=%s"
        )
        params = (to, to, _now(), run_id, expected) if visible else (to, _now(), run_id, expected)
        with self._pool.connection() as connection:
            return bool(connection.execute(sql, params).rowcount == 1)

    def release(self, run_id: str, status: str) -> None:
        with self._pool.connection() as connection:
            connection.execute(
                "UPDATE incidents SET status=%s, updated_at=%s WHERE run_id=%s AND status='busy'",
                (status, _now(), run_id),
            )

    def add_audit(
        self,
        run_id: str,
        event: str,
        actor: str,
        detail: dict[str, Any] | None = None,
        tenant: str | None = None,
    ) -> None:
        """Append to the tenant's chain. The tenant is the run's own unless given explicitly."""
        detail_json = json.dumps(detail or {}, default=str, sort_keys=True)
        with self._pool.connection() as connection, connection.transaction():
            if tenant is None:
                owner = connection.execute(
                    "SELECT tenant_id FROM incidents WHERE run_id = %s", (run_id,)
                ).fetchone()
                tenant = owner["tenant_id"] if owner else DEFAULT_TENANT
            connection.execute(
                "INSERT INTO audit_heads (tenant_id, seq, hash) VALUES (%s, 0, '') "
                "ON CONFLICT (tenant_id) DO NOTHING",
                (tenant,),
            )
            # The row lock serialises writers of this tenant's chain across every replica.
            head = connection.execute(
                "SELECT seq, hash FROM audit_heads WHERE tenant_id = %s FOR UPDATE", (tenant,)
            ).fetchone()
            seq, prev, at = int(head["seq"]) + 1, head["hash"], _now()
            digest = entry_hash(prev, canonical(tenant, seq, run_id, event, actor, detail_json, at))
            connection.execute(
                "INSERT INTO audit_log (run_id, event, actor, detail_json, at, tenant_id, seq, "
                "prev_hash, hash) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (run_id, event, actor, detail_json, at, tenant, seq, prev, digest),
            )
            connection.execute(
                "UPDATE audit_heads SET seq = %s, hash = %s WHERE tenant_id = %s",
                (seq, digest, tenant),
            )

    def audit_for(self, run_id: str, *, tenant: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT event, actor, detail_json, at FROM audit_log WHERE run_id=%s"
        params: list[Any] = [run_id]
        if tenant is not None:
            sql += " AND tenant_id=%s"
            params.append(tenant)
        with self._pool.connection() as connection:
            rows = connection.execute(sql + " ORDER BY id", params).fetchall()
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
        state["tenant_id"] = row["tenant_id"]
        return state
