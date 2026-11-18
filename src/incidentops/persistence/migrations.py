"""Versioned schema migrations for the SQLite and Postgres read-models.

Each migration carries one script per dialect. Applied versions are recorded in
``schema_migrations``, so startup only runs what is missing. Rules for new migrations:
append to ``MIGRATIONS`` with the next version, never edit an applied one, and keep the SQLite
and Postgres scripts equivalent. Databases created before this module existed already hold the
version-1 tables, which is why version 1 is written with ``IF NOT EXISTS``.

On Postgres a session advisory lock serialises concurrent replicas that start together.
"""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

# Arbitrary constant: all IncidentOps instances contend for the same migration lock.
_ADVISORY_LOCK_KEY = 0x1D0C_0D5

_TRACKING_SQLITE = (
    "CREATE TABLE IF NOT EXISTS schema_migrations ("
    "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
)
_TRACKING_POSTGRES = (
    "CREATE TABLE IF NOT EXISTS schema_migrations ("
    "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
)


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sqlite: str
    postgres: str


_V1_SQLITE = """
CREATE TABLE IF NOT EXISTS incidents (
    run_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    severity TEXT,
    service TEXT,
    summary TEXT,
    state_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status, updated_at DESC);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    event TEXT NOT NULL,
    actor TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_run ON audit_log(run_id, id);
"""

_V1_POSTGRES = """
CREATE TABLE IF NOT EXISTS incidents (
    run_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    severity TEXT,
    service TEXT,
    summary TEXT,
    state_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status, updated_at DESC);
CREATE TABLE IF NOT EXISTS audit_log (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL,
    event TEXT NOT NULL,
    actor TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_run ON audit_log(run_id, id);
"""

MIGRATIONS: tuple[Migration, ...] = (Migration(1, "initial schema", _V1_SQLITE, _V1_POSTGRES),)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _validate() -> None:
    versions = [migration.version for migration in MIGRATIONS]
    if versions != list(range(1, len(versions) + 1)):
        raise RuntimeError(f"migration versions must be 1..N with no gaps, got {versions}")


def migrate_sqlite(connection: sqlite3.Connection) -> list[int]:
    """Apply pending migrations. Returns the versions applied in this call."""
    _validate()
    connection.execute(_TRACKING_SQLITE)
    connection.commit()
    applied = {row[0] for row in connection.execute("SELECT version FROM schema_migrations")}
    ran: list[int] = []
    for migration in MIGRATIONS:
        if migration.version in applied:
            continue
        # One script per migration, wrapped in an explicit transaction together with its
        # bookkeeping row, so a failure leaves neither half applied.
        try:
            connection.executescript(
                f"BEGIN;\n{migration.sqlite}\n"
                f"INSERT INTO schema_migrations (version, name, applied_at) "
                f"VALUES ({migration.version}, '{migration.name}', '{_now()}');\nCOMMIT;"
            )
        except sqlite3.Error:
            # executescript stops at the failing statement and leaves BEGIN open. Without this
            # rollback the half-applied script would be committed when the connection closes.
            connection.rollback()
            raise
        ran.append(migration.version)
    return ran


def migrate_postgres(connection: Any) -> list[int]:
    """Apply pending migrations on an autocommit psycopg connection, under an advisory lock."""
    _validate()
    connection.execute("SELECT pg_advisory_lock(%s)", (_ADVISORY_LOCK_KEY,))
    try:
        connection.execute(_TRACKING_POSTGRES)
        applied = {
            row[0] if not isinstance(row, dict) else row["version"]
            for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
        }
        ran: list[int] = []
        for migration in MIGRATIONS:
            if migration.version in applied:
                continue
            with connection.transaction():
                connection.execute(migration.postgres)
                connection.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (%s, %s, %s)",
                    (migration.version, migration.name, _now()),
                )
            ran.append(migration.version)
        return ran
    finally:
        connection.execute("SELECT pg_advisory_unlock(%s)", (_ADVISORY_LOCK_KEY,))
