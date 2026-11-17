"""Backward-compatible re-exports. Use :mod:`incidentops.rag` for new code."""

from incidentops.rag.hybrid import index_runbooks_with_embeddings, search_runbooks_hybrid
from incidentops.rag.retriever import (
    FtsRetriever,
    HybridRetriever,
    RunbookRetrievalMode,
    RunbookRetriever,
    build_runbook_retriever,
    index_runbooks_for_settings,
)

__all__ = [
    "FtsRetriever",
    "HybridRetriever",
    "RunbookRetriever",
    "RunbookRetrievalMode",
    "build_runbook_retriever",
    "index_runbooks_for_settings",
    "index_runbooks_with_embeddings",
    "search_runbooks_hybrid",
]
