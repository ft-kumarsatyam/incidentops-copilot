"""Runbook RAG: chunking, lexical search, query rewrite, optional hybrid rerank.

Production path: expand query (rewrite) → FTS BM25 → cite chunks in triage.
Hybrid mode adds dense rerank for vocabulary mismatch; see ``docs/RAG_SYSTEM_DESIGN.md``.
"""

from incidentops.rag.fts import index_runbooks, search_runbooks
from incidentops.rag.hybrid import index_runbooks_with_embeddings, search_runbooks_hybrid
from incidentops.rag.pgvector import PostgresRetriever, index_runbooks_postgres
from incidentops.rag.retriever import (
    FtsRetriever,
    HybridRetriever,
    RunbookRetrievalMode,
    RunbookRetriever,
    build_runbook_retriever,
    index_runbooks_for_settings,
    runbook_index_status,
)
from incidentops.rag.rewrite import expand_runbook_query, rewrite_metadata

__all__ = [
    "FtsRetriever",
    "HybridRetriever",
    "PostgresRetriever",
    "RunbookRetriever",
    "RunbookRetrievalMode",
    "build_runbook_retriever",
    "expand_runbook_query",
    "index_runbooks",
    "index_runbooks_for_settings",
    "index_runbooks_postgres",
    "index_runbooks_with_embeddings",
    "rewrite_metadata",
    "runbook_index_status",
    "search_runbooks",
    "search_runbooks_hybrid",
]
