"""SQLite read-model for incidents and a hash-chained, append-only audit log.

LangGraph's checkpointer (same database file) is the source of truth for workflow state.
This repository stores a queryable copy for listing/search and enforces the
``awaiting_approval -> busy`` claim that makes decisions safe under concurrency. Every incident
row carries a ``tenant_id``; reads that pass ``tenant`` only ever see that tenant's rows.
"""

import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from incidentops.persistence.audit import (
    DEFAULT_TENANT,
    ApprovalQueries,
    AuditQueries,
    canonical,
    entry_hash,
)
from incidentops.persistence.keys import KeyQueries
from incidentops.persistence.migrations import migrate_sqlite

_STATE_COLUMNS = "state_json, created_at, updated_at, tenant_id"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class IncidentRepo(AuditQueries, ApprovalQueries, KeyQueries):
    def __init__(self, database: Path) -> None:
        self.database = database
        database.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            migrate_sqlite(connection)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.database, timeout=10)) as connection:
            connection.row_factory = sqlite3.Row
            with connection:
                yield connection

    def close(self) -> None:
        """Connections are per-operation, so there is nothing to release."""

    def ping(self) -> bool:
        with self._connect() as connection:
            connection.execute("SELECT 1")
        return True

    # -- AuditQueries plumbing -------------------------------------------------------------
    def _rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[Mapping[str, Any]]:
        with self._connect() as connection:
            return list(connection.execute(sql, params).fetchall())

    def _exec(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        with self._connect() as connection:
            return connection.execute(sql, params).rowcount

    # -- incidents -------------------------------------------------------------------------
    def upsert(self, state: dict[str, Any], tenant: str = DEFAULT_TENANT) -> None:
        """Insert or update a run. ``tenant`` applies to a new row only: a run never moves."""
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO incidents
                    (run_id, status, severity, service, summary, state_json, created_at,
                     updated_at, tenant_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status=excluded.status, severity=excluded.severity,
                    service=excluded.service, summary=excluded.summary,
                    state_json=excluded.state_json, updated_at=excluded.updated_at
                """,
                (*self._values(state, now), tenant),
            )

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

    def get(self, run_id: str, *, tenant: str | None = None) -> dict[str, Any] | None:
        sql = f"SELECT {_STATE_COLUMNS} FROM incidents WHERE run_id = ?"
        params: list[Any] = [run_id]
        if tenant is not None:
            sql += " AND tenant_id = ?"
            params.append(tenant)
        with self._connect() as connection:
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
            clauses.append("tenant_id = ?")
            params.append(tenant)
        if statuses:
            clauses.append(f"status IN ({','.join('?' * len(statuses))})")
            params.extend(statuses)
        if query:
            clauses.append("(summary LIKE ? OR service LIKE ? OR run_id LIKE ?)")
            like = f"%{query}%"
            params.extend([like, like, like])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM incidents {where}", params
            ).fetchone()[0]
            rows = connection.execute(
                f"SELECT {_STATE_COLUMNS} FROM incidents {where} "
                "ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return [self._hydrate(row) for row in rows], int(total)

    def insert_new(self, state: dict[str, Any], tenant: str = DEFAULT_TENANT) -> bool:
        """Create the run only if it does not exist. False means someone else created it."""
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO incidents (run_id, status, severity, service, summary, state_json, "
                "created_at, updated_at, tenant_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(run_id) DO NOTHING",
                (*self._values(state, _now()), tenant),
            )
            return cursor.rowcount == 1

    def claim(self, run_id: str, expected: str, to: str = "busy", *, visible: bool = False) -> bool:
        """Atomically move ``expected`` -> ``to``. Only one concurrent caller can win.

        Reads come from the stored state, so by default only the status column changes (the
        lock use: `busy` is never shown). With ``visible`` the stored state is patched in the
        same statement, so readers see the new status immediately.
        """
        sql = (
            "UPDATE incidents SET status=?, state_json=json_set(state_json, '$.status', ?), "
            "updated_at=? WHERE run_id=? AND status=?"
            if visible
            else "UPDATE incidents SET status=?, updated_at=? WHERE run_id=? AND status=?"
        )
        params = (to, to, _now(), run_id, expected) if visible else (to, _now(), run_id, expected)
        with self._connect() as connection:
            return connection.execute(sql, params).rowcount == 1

    def release(self, run_id: str, status: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE incidents SET status=?, updated_at=? WHERE run_id=? AND status='busy'",
                (status, _now(), run_id),
            )

    # -- audit -----------------------------------------------------------------------------
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
        with closing(sqlite3.connect(self.database, timeout=10)) as connection:
            connection.row_factory = sqlite3.Row
            connection.isolation_level = None  # explicit transaction below
            connection.execute("BEGIN IMMEDIATE")  # one writer at a time: the chain is serial
            try:
                if tenant is None:
                    owner = connection.execute(
                        "SELECT tenant_id FROM incidents WHERE run_id = ?", (run_id,)
                    ).fetchone()
                    tenant = owner["tenant_id"] if owner else DEFAULT_TENANT
                connection.execute(
                    "INSERT OR IGNORE INTO audit_heads (tenant_id, seq, hash) VALUES (?, 0, '')",
                    (tenant,),
                )
                head = connection.execute(
                    "SELECT seq, hash FROM audit_heads WHERE tenant_id = ?", (tenant,)
                ).fetchone()
                seq, prev, at = head["seq"] + 1, head["hash"], _now()
                digest = entry_hash(
                    prev, canonical(tenant, seq, run_id, event, actor, detail_json, at)
                )
                connection.execute(
                    "INSERT INTO audit_log (run_id, event, actor, detail_json, at, tenant_id, "
                    "seq, prev_hash, hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (run_id, event, actor, detail_json, at, tenant, seq, prev, digest),
                )
                connection.execute(
                    "UPDATE audit_heads SET seq = ?, hash = ? WHERE tenant_id = ?",
                    (seq, digest, tenant),
                )
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise

    def audit_for(self, run_id: str, *, tenant: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT event, actor, detail_json, at FROM audit_log WHERE run_id=?"
        params: list[Any] = [run_id]
        if tenant is not None:
            sql += " AND tenant_id=?"
            params.append(tenant)
        with self._connect() as connection:
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
    def _hydrate(row: sqlite3.Row) -> dict[str, Any]:
        state: dict[str, Any] = json.loads(row["state_json"])
        state["created_at"] = row["created_at"]
        state["updated_at"] = row["updated_at"]
        state["tenant_id"] = row["tenant_id"]
        return state
