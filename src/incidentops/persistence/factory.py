"""Pick the storage backend from settings: Postgres when DATABASE_URL is set, else SQLite."""

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite import SqliteSaver

from incidentops.core.config import Settings
from incidentops.persistence.base import Repo
from incidentops.persistence.jobs import JobStore, PostgresJobStore, SqliteJobStore
from incidentops.persistence.postgres import PostgresIncidentRepo, open_pool
from incidentops.persistence.repo import IncidentRepo


@dataclass
class Storage:
    """The read-model repo and the LangGraph checkpointer, backed by the same database."""

    repo: Repo
    jobs: JobStore
    checkpointer: BaseCheckpointSaver[Any]
    _release: Callable[[], None]
    backend: str

    def close(self) -> None:
        self._release()


def open_repo(settings: Settings) -> Repo:
    """Just the read-model, for read-only callers such as the CLI digest."""
    if settings.database_url:
        return PostgresIncidentRepo(open_pool(settings.database_url, max_size=2))
    return IncidentRepo(settings.database_path)


_CHECKPOINT_SETUP_LOCK = 0x1D0C_0D6  # distinct from the schema-migration lock


def _setup_checkpointer(saver: Any, database_url: str, *, timeout: float = 120.0) -> None:
    """Create LangGraph's tables once, even when replicas start at the same moment.

    ``PostgresSaver.setup()`` is not safe to run concurrently (two ``CREATE TABLE IF NOT EXISTS``
    race on the catalog and one replica crashes), so it runs under an advisory lock.

    Waiters *poll* with ``pg_try_advisory_lock`` instead of blocking in ``pg_advisory_lock``: setup
    runs ``CREATE INDEX CONCURRENTLY``, which waits for every other running statement to finish, so
    a waiter parked inside a blocking lock call would deadlock it against the lock holder.
    """
    import psycopg

    with psycopg.connect(database_url, autocommit=True) as lock:
        deadline = time.monotonic() + timeout
        while True:
            row = lock.execute("SELECT pg_try_advisory_lock(%s)", (_CHECKPOINT_SETUP_LOCK,))
            if (row.fetchone() or (False,))[0]:
                break
            if time.monotonic() > deadline:
                raise TimeoutError("another replica is still setting up the checkpoint tables")
            time.sleep(0.05)
        try:
            saver.setup()
        finally:
            lock.execute("SELECT pg_advisory_unlock(%s)", (_CHECKPOINT_SETUP_LOCK,))


def open_storage(settings: Settings) -> Storage:
    if settings.database_url:
        from langgraph.checkpoint.postgres import PostgresSaver

        pool = open_pool(settings.database_url, max_size=settings.database_pool_size)
        saver = PostgresSaver(pool)
        _setup_checkpointer(saver, settings.database_url)
        return Storage(
            PostgresIncidentRepo(pool), PostgresJobStore(pool), saver, pool.close, "postgres"
        )

    repo = IncidentRepo(settings.database_path)
    connection = sqlite3.connect(settings.database_path, check_same_thread=False)
    connection.execute("PRAGMA journal_mode=WAL")
    return Storage(
        repo,
        SqliteJobStore(settings.database_path),
        SqliteSaver(connection),
        connection.close,
        "sqlite",
    )
