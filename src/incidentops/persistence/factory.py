"""Pick the storage backend from settings: Postgres when DATABASE_URL is set, else SQLite."""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite import SqliteSaver

from incidentops.core.config import Settings
from incidentops.persistence.base import Repo
from incidentops.persistence.postgres import PostgresIncidentRepo, open_pool
from incidentops.persistence.repo import IncidentRepo


@dataclass
class Storage:
    """The read-model repo and the LangGraph checkpointer, backed by the same database."""

    repo: Repo
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


def open_storage(settings: Settings) -> Storage:
    if settings.database_url:
        from langgraph.checkpoint.postgres import PostgresSaver

        pool = open_pool(settings.database_url, max_size=settings.database_pool_size)
        saver = PostgresSaver(pool)
        saver.setup()
        return Storage(PostgresIncidentRepo(pool), saver, pool.close, "postgres")

    repo = IncidentRepo(settings.database_path)
    connection = sqlite3.connect(settings.database_path, check_same_thread=False)
    connection.execute("PRAGMA journal_mode=WAL")
    return Storage(repo, SqliteSaver(connection), connection.close, "sqlite")
