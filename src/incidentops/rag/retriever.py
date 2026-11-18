"""Public retrieval interface: pick FTS or hybrid from settings."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Protocol

from incidentops.core.config import Settings
from incidentops.rag.fts import index_runbooks, search_runbooks
from incidentops.rag.hybrid import index_runbooks_with_embeddings, search_runbooks_hybrid
from incidentops.rag.pgvector import PostgresRetriever, index_runbooks_postgres, index_status

RunbookRetrievalMode = Literal["fts", "hybrid"]


class RunbookRetriever(Protocol):
    def search(
        self,
        query: str,
        limit: int = 3,
        prefer: str | None = None,
    ) -> list[dict[str, Any]]: ...


class FtsRetriever:
    def __init__(self, database: Path) -> None:
        self._database = database

    def search(
        self,
        query: str,
        limit: int = 3,
        prefer: str | None = None,
    ) -> list[dict[str, Any]]:
        return search_runbooks(self._database, query, limit, prefer=prefer)


class HybridRetriever:
    def __init__(self, database: Path) -> None:
        self._database = database

    def search(
        self,
        query: str,
        limit: int = 3,
        prefer: str | None = None,
    ) -> list[dict[str, Any]]:
        return search_runbooks_hybrid(self._database, query, limit, prefer=prefer)


def _database_url(settings: Settings) -> str:
    if not settings.database_url:
        raise ValueError("RUNBOOK_STORE=postgres needs DATABASE_URL (the index lives there)")
    return settings.database_url


def index_runbooks_for_settings(settings: Settings) -> dict[str, int]:
    if settings.runbook_store == "postgres":
        return index_runbooks_postgres(
            settings.runbook_dir,
            _database_url(settings),
            embeddings=settings.runbook_retrieval == "hybrid",
        )
    if settings.runbook_retrieval == "hybrid":
        return index_runbooks_with_embeddings(settings.runbook_dir, settings.runbook_db)
    count = index_runbooks(settings.runbook_dir, settings.runbook_db)
    return {"indexed_chunks": count}


def runbook_index_status(settings: Settings) -> str:
    """``ok``, ``missing`` (not built yet) or ``unavailable`` (the store cannot be reached)."""
    if settings.runbook_store == "postgres":
        try:
            return index_status(_database_url(settings))
        except Exception:
            return "unavailable"
    return "ok" if settings.runbook_db.is_file() else "missing"


def build_runbook_retriever(settings: Settings) -> RunbookRetriever:
    if settings.runbook_store == "postgres":
        return PostgresRetriever(
            _database_url(settings), hybrid=settings.runbook_retrieval == "hybrid"
        )
    if settings.runbook_retrieval == "hybrid":
        return HybridRetriever(settings.runbook_db)
    return FtsRetriever(settings.runbook_db)
