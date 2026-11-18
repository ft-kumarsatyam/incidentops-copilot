"""SQLite read-model for incidents and an append-only audit log.

LangGraph's checkpointer (same database file) is the source of truth for workflow state.
This repository stores a queryable copy for listing/search and enforces the
``awaiting_approval -> busy`` claim that makes decisions safe under concurrency.
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from incidentops.persistence.migrations import migrate_sqlite


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class IncidentRepo:
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

    def upsert(self, state: dict[str, Any]) -> None:
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO incidents
                    (run_id, status, severity, service, summary, state_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    status=excluded.status, severity=excluded.severity,
                    service=excluded.service, summary=excluded.summary,
                    state_json=excluded.state_json, updated_at=excluded.updated_at
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
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state_json, created_at, updated_at FROM incidents WHERE run_id = ?",
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
                f"SELECT state_json, created_at, updated_at FROM incidents {where} "
                "ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
        return [self._hydrate(row) for row in rows], int(total)

    def claim(self, run_id: str, expected: str) -> bool:
        """Atomically move ``expected`` -> ``busy``. Only one concurrent caller can win."""
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE incidents SET status='busy', updated_at=? WHERE run_id=? AND status=?",
                (_now(), run_id, expected),
            )
            return cursor.rowcount == 1

    def release(self, run_id: str, status: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE incidents SET status=?, updated_at=? WHERE run_id=? AND status='busy'",
                (status, _now(), run_id),
            )

    def add_audit(
        self, run_id: str, event: str, actor: str, detail: dict[str, Any] | None = None
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO audit_log (run_id, event, actor, detail_json, at) "
                "VALUES (?, ?, ?, ?, ?)",
                (run_id, event, actor, json.dumps(detail or {}, default=str), _now()),
            )

    def audit_for(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT event, actor, detail_json, at FROM audit_log WHERE run_id=? ORDER BY id",
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
    def _hydrate(row: sqlite3.Row) -> dict[str, Any]:
        state: dict[str, Any] = json.loads(row["state_json"])
        state["created_at"] = row["created_at"]
        state["updated_at"] = row["updated_at"]
        return state
