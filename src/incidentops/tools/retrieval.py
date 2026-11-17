"""Runbook retrieval backends: FTS (default) and optional hybrid dense rerank."""

from __future__ import annotations

import math
import sqlite3
import struct
from contextlib import closing
from pathlib import Path
from typing import Any, Literal, Protocol

from incidentops.core.config import Settings
from incidentops.tools.runbooks import index_runbooks, search_runbooks

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


def _load_embedder() -> Any:
    try:
        from fastembed import TextEmbedding
    except ImportError as error:
        raise RuntimeError(
            "Hybrid runbook retrieval requires the [rag] extra: "
            "pip install 'incidentops-copilot[rag]'"
        ) from error
    return TextEmbedding(model_name="BAAI/bge-small-en-v1.5")


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0:
        return vector
    return [x / norm for x in vector]


def _pack_vector(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def _unpack_vector(blob: bytes) -> list[float]:
    count = len(blob) // 4
    return list(struct.unpack(f"{count}f", blob))


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def _ensure_embedding_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TABLE IF NOT EXISTS runbook_embeddings ("
        "path TEXT NOT NULL, title TEXT NOT NULL, content TEXT NOT NULL, "
        "embedding BLOB NOT NULL, PRIMARY KEY (path, title))"
    )


def index_runbooks_with_embeddings(directory: Path, database: Path) -> dict[str, int]:
    """Rebuild FTS and dense vectors in one database file."""
    chunks = index_runbooks(directory, database)
    embedder = _load_embedder()
    texts: list[tuple[str, str, str]] = []
    with closing(sqlite3.connect(database)) as connection, connection:
        rows = connection.execute(
            "SELECT path, title, content FROM runbook_chunks"
        ).fetchall()
        texts = [(str(r[0]), str(r[1]), str(r[2])) for r in rows]
        _ensure_embedding_schema(connection)
        connection.execute("DELETE FROM runbook_embeddings")
    vectors = list(embedder.embed(f"{title}\n{content}" for _, title, content in texts))
    with closing(sqlite3.connect(database)) as connection, connection:
        for (path, title, content), raw in zip(texts, vectors, strict=True):
            vec = _normalize(list(raw))
            connection.execute(
                "INSERT INTO runbook_embeddings"
                "(path, title, content, embedding) VALUES (?, ?, ?, ?)",
                (path, title, content, _pack_vector(vec)),
            )
    return {"indexed_chunks": chunks, "indexed_embeddings": len(texts)}


def search_runbooks_hybrid(
    database: Path,
    query: str,
    limit: int = 3,
    prefer: str | None = None,
    *,
    candidate_limit: int = 20,
) -> list[dict[str, Any]]:
    """FTS shortlist, then dense rerank; shape matches ``search_runbooks``."""
    if limit < 1 or not database.is_file():
        return []
    fts_hits = search_runbooks(database, query, candidate_limit, prefer=None)
    embedder = _load_embedder()
    query_vec = _normalize(list(next(embedder.embed([query]))))
    with closing(sqlite3.connect(database)) as connection:
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute(
                "SELECT path, title, content, embedding FROM runbook_embeddings"
            ).fetchall()
        except sqlite3.Error:
            return fts_hits[:limit]
    vec_by_key = {
        (row["path"], row["title"]): _unpack_vector(row["embedding"]) for row in rows
    }
    scored: list[tuple[float, dict[str, Any]]] = []
    name = f"{prefer.strip().lower()}.md" if prefer else ""
    for rank, hit in enumerate(fts_hits):
        key = (hit["path"], hit["title"])
        dense = _cosine(query_vec, vec_by_key[key]) if key in vec_by_key else 0.0
        fts_prior = 1.0 / (1.0 + rank)
        prefer_boost = 1.0 if name and (
            hit["path"].lower() == name or hit["path"].lower().endswith("/" + name)
        ) else 0.0
        combined = 0.55 * dense + 0.35 * fts_prior + 0.10 * prefer_boost
        scored.append(
            (
                combined,
                {
                    **hit,
                    "score": combined,
                    "dense_score": dense,
                },
            )
        )
    if not scored and rows:
        # No FTS hits: brute-force dense search over the corpus (small N).
        for row in rows:
            dense = _cosine(query_vec, _unpack_vector(row["embedding"]))
            scored.append(
                (
                    dense,
                    {
                        "path": row["path"],
                        "title": row["title"],
                        "excerpt": row["content"][:160] + "…",
                        "score": dense,
                        "dense_score": dense,
                    },
                )
            )
    scored.sort(key=lambda item: item[0], reverse=True)
    return [hit for _, hit in scored[:limit]]


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