"""Public retrieval interface: pick FTS or hybrid from settings."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Protocol

from incidentops.core.config import Settings
from incidentops.rag.fts import index_runbooks, search_runbooks
from incidentops.rag.hybrid import index_runbooks_with_embeddings, search_runbooks_hybrid

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


def index_runbooks_for_settings(settings: Settings) -> dict[str, int]:
    if settings.runbook_retrieval == "hybrid":
        return index_runbooks_with_embeddings(settings.runbook_dir, settings.runbook_db)
    count = index_runbooks(settings.runbook_dir, settings.runbook_db)
    return {"indexed_chunks": count}


def build_runbook_retriever(settings: Settings) -> RunbookRetriever:
    if settings.runbook_retrieval == "hybrid":
        return HybridRetriever(settings.runbook_db)
    return FtsRetriever(settings.runbook_db)
