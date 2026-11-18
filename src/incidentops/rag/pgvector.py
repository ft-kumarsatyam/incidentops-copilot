"""Runbook index in Postgres: full-text search, plus pgvector for hybrid retrieval.

The SQLite index is one file per instance. This one lives in ``DATABASE_URL`` so every replica
searches the same corpus and an index built once is visible everywhere.

* Lexical: a ``tsvector`` column with a GIN index, ranked with ``ts_rank_cd``.
* Dense (hybrid mode): a ``vector(384)`` column with an HNSW index (cosine).
* Hybrid scores nearest-neighbour candidates *and* full-text candidates with the same weights as
  the SQLite hybrid, so a query with no lexical overlap can still find its runbook.
* Reindexing is cheap to repeat and safe to race: it is skipped when the corpus hash is unchanged,
  and the rewrite itself holds an advisory lock, so replicas starting together do not thrash it.

Needs the ``postgres`` extra, and for hybrid also the ``rag`` extra and a Postgres with the pgvector
extension (for example the ``pgvector/pgvector:pg17`` image).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

from incidentops.rag.fts import WORD, load_chunks
from incidentops.rag.hybrid import _load_embedder, _normalize

EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
EMBEDDING_DIMS = 384
CANDIDATES = 20
_LOCK_KEY = 7_281_904_551  # one advisory lock id for every runbook index change

Embedder = Callable[[Sequence[str]], list[list[float]]]

_TABLES = """
CREATE TABLE IF NOT EXISTS runbook_chunks (
  path text NOT NULL,
  ord integer NOT NULL,
  doc text NOT NULL,
  title text NOT NULL,
  content text NOT NULL,
  tsv tsvector GENERATED ALWAYS AS (
    setweight(to_tsvector('english', doc), 'B')
    || setweight(to_tsvector('english', title), 'B')
    || setweight(to_tsvector('english', content), 'D')
  ) STORED,
  PRIMARY KEY (path, ord)
);
CREATE INDEX IF NOT EXISTS runbook_chunks_tsv ON runbook_chunks USING gin (tsv);
CREATE TABLE IF NOT EXISTS runbook_index_meta (
  id integer PRIMARY KEY CHECK (id = 1),
  corpus_hash text NOT NULL,
  embedding_model text,
  indexed_at timestamptz NOT NULL DEFAULT now()
);
"""

_VECTOR_COLUMN = f"""
ALTER TABLE runbook_chunks ADD COLUMN IF NOT EXISTS embedding vector({EMBEDDING_DIMS});
CREATE INDEX IF NOT EXISTS runbook_chunks_embedding
  ON runbook_chunks USING hnsw (embedding vector_cosine_ops);
"""


def _psycopg() -> Any:
    try:
        import psycopg
    except ImportError as error:
        raise RuntimeError(
            "RUNBOOK_STORE=postgres needs the postgres extra: pip install 'incidentops[postgres]'"
        ) from error
    return psycopg


@lru_cache(maxsize=1)
def default_embedder() -> Embedder:
    """fastembed's small English model, loaded once per process."""
    model = _load_embedder()

    def embed(texts: Sequence[str]) -> list[list[float]]:
        return [_normalize(list(vector)) for vector in model.embed(list(texts))]

    return embed


def _literal(vector: Sequence[float]) -> str:
    """A pgvector text literal, so no client-side vector adapter is needed."""
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


def _corpus_hash(rows: list[tuple[str, str, str, str]], embeddings: bool) -> str:
    digest = hashlib.sha256(f"{EMBEDDING_MODEL}:{EMBEDDING_DIMS}:{embeddings}".encode())
    for row in rows:
        for part in row:
            digest.update(part.encode("utf-8"))
            digest.update(b"\x00")
    return digest.hexdigest()


def _ensure_schema(connection: Any, embeddings: bool) -> None:
    psycopg = _psycopg()
    with connection.transaction():
        connection.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
        if embeddings:
            try:
                connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
            except psycopg.errors.FeatureNotSupported as error:
                raise RuntimeError(
                    "pgvector is not available in this Postgres. Use an image that ships it "
                    "(for example pgvector/pgvector:pg17) or set RUNBOOK_RETRIEVAL=fts."
                ) from error
        connection.execute(_TABLES)
        if embeddings:
            connection.execute(_VECTOR_COLUMN)


def _stored_hash(connection: Any) -> str | None:
    row = connection.execute("SELECT corpus_hash FROM runbook_index_meta WHERE id = 1").fetchone()
    return None if row is None else str(row[0])


def index_runbooks_postgres(
    directory: Path,
    database_url: str,
    *,
    embeddings: bool = False,
    embedder: Embedder | None = None,
    force: bool = False,
) -> dict[str, int]:
    """Build the shared index. Returns counts; ``skipped`` is 1 when nothing had changed."""
    psycopg = _psycopg()
    rows = load_chunks(directory)
    digest = _corpus_hash(rows, embeddings)
    with psycopg.connect(database_url, autocommit=True) as connection:
        _ensure_schema(connection, embeddings)
        if not force and _stored_hash(connection) == digest:
            return {"indexed_chunks": len(rows), "indexed_embeddings": 0, "skipped": 1}
        vectors: list[list[float]] = []
        if embeddings:
            embed = embedder or default_embedder()
            vectors = embed([f"{title}\n{content}" for _, _, title, content in rows])
        with connection.transaction():
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
            if not force and _stored_hash(connection) == digest:  # another replica just finished
                return {"indexed_chunks": len(rows), "indexed_embeddings": 0, "skipped": 1}
            connection.execute("DELETE FROM runbook_chunks")
            ordinal: dict[str, int] = {}
            for index, (path, doc, title, content) in enumerate(rows):
                position = ordinal[path] = ordinal.get(path, -1) + 1
                if embeddings:
                    connection.execute(
                        "INSERT INTO runbook_chunks (path, ord, doc, title, content, embedding) "
                        "VALUES (%s, %s, %s, %s, %s, %s::vector)",
                        (path, position, doc, title, content, _literal(vectors[index])),
                    )
                else:
                    connection.execute(
                        "INSERT INTO runbook_chunks (path, ord, doc, title, content) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (path, position, doc, title, content),
                    )
            connection.execute(
                "INSERT INTO runbook_index_meta (id, corpus_hash, embedding_model) "
                "VALUES (1, %s, %s) ON CONFLICT (id) DO UPDATE SET "
                "corpus_hash = EXCLUDED.corpus_hash, embedding_model = EXCLUDED.embedding_model, "
                "indexed_at = now()",
                (digest, EMBEDDING_MODEL if embeddings else None),
            )
    return {
        "indexed_chunks": len(rows),
        "indexed_embeddings": len(rows) if embeddings else 0,
        "skipped": 0,
    }


def index_status(database_url: str) -> str:
    """``ok`` when the shared index has chunks, ``missing`` when it is empty or not built yet."""
    psycopg = _psycopg()
    try:
        with psycopg.connect(database_url, connect_timeout=3) as connection:
            row = connection.execute("SELECT count(*) FROM runbook_chunks").fetchone()
    except psycopg.errors.UndefinedTable:
        return "missing"
    return "ok" if row and row[0] else "missing"


def _prefer_name(prefer: str | None) -> str:
    return f"{prefer.strip().lower()}.md" if prefer and prefer.strip() else ""


def _is_preferred(path: str, name: str) -> bool:
    lowered = path.lower()
    return bool(name) and (lowered == name or lowered.endswith("/" + name))


class PostgresRetriever:
    """The ``RunbookRetriever`` over the shared Postgres index."""

    def __init__(
        self, database_url: str, *, hybrid: bool = False, embedder: Embedder | None = None
    ) -> None:
        self._url = database_url
        self._hybrid = hybrid
        self._embedder = embedder

    def search(self, query: str, limit: int = 3, prefer: str | None = None) -> list[dict[str, Any]]:
        terms = list(dict.fromkeys(word.lower() for word in WORD.findall(query)))[:12]
        if limit < 1 or (not terms and not self._hybrid):
            return []
        psycopg = _psycopg()
        tsquery = " | ".join(f"'{term}'" for term in terms)
        name = _prefer_name(prefer)
        vector = None
        if self._hybrid:
            embed = self._embedder or default_embedder()
            vector = _literal(embed([query])[0])
        try:
            with psycopg.connect(self._url, connect_timeout=3) as connection:
                if vector is None:
                    return self._lexical(connection, tsquery, limit, name)
                return self._hybrid_search(connection, tsquery, vector, limit, name)
        except psycopg.errors.UndefinedTable:
            return []  # index not built yet: same as the SQLite store with no file

    @staticmethod
    def _lexical(connection: Any, tsquery: str, limit: int, name: str) -> list[dict[str, Any]]:
        rows = connection.execute(
            "SELECT path, title, ts_headline('english', content, q, "
            "'StartSel=[, StopSel=], MaxFragments=1, MaxWords=24, MinWords=8') AS excerpt, "
            "ts_rank_cd(tsv, q, 32) AS score "
            "FROM runbook_chunks, to_tsquery('english', %s) AS q WHERE tsv @@ q "
            "ORDER BY score DESC LIMIT %s",
            (tsquery, CANDIDATES if name else limit),
        ).fetchall()
        hits = [{"path": r[0], "title": r[1], "excerpt": r[2], "score": float(r[3])} for r in rows]
        hits.sort(key=lambda hit: not _is_preferred(str(hit["path"]), name))  # stable: keeps rank
        return hits[:limit]

    @staticmethod
    def _hybrid_search(
        connection: Any, tsquery: str, vector: str, limit: int, name: str
    ) -> list[dict[str, Any]]:
        candidates: dict[tuple[str, int], dict[str, Any]] = {}
        if tsquery:
            rows = connection.execute(
                "SELECT path, ord, title, ts_headline('english', content, q, "
                "'StartSel=[, StopSel=], MaxFragments=1, MaxWords=24, MinWords=8'), "
                "1 - (embedding <=> %s::vector) FROM runbook_chunks, "
                "to_tsquery('english', %s) AS q WHERE tsv @@ q "
                "ORDER BY ts_rank_cd(tsv, q, 32) DESC LIMIT %s",
                (vector, tsquery, CANDIDATES),
            ).fetchall()
            for rank, row in enumerate(rows):
                candidates[(row[0], row[1])] = {
                    "path": row[0], "title": row[2], "excerpt": row[3],
                    "dense_score": float(row[4] or 0.0), "fts_prior": 1.0 / (1.0 + rank),
                }  # fmt: skip
        rows = connection.execute(
            "SELECT path, ord, title, content, 1 - (embedding <=> %s::vector) "
            "FROM runbook_chunks WHERE embedding IS NOT NULL "
            "ORDER BY embedding <=> %s::vector LIMIT %s",
            (vector, vector, CANDIDATES),
        ).fetchall()
        for row in rows:
            candidates.setdefault(
                (row[0], row[1]),
                {
                    "path": row[0], "title": row[2], "excerpt": str(row[3])[:160] + "…",
                    "dense_score": float(row[4] or 0.0), "fts_prior": 0.0,
                },
            )  # fmt: skip
        hits = []
        for hit in candidates.values():
            boost = 1.0 if _is_preferred(str(hit["path"]), name) else 0.0
            score = 0.55 * hit["dense_score"] + 0.35 * hit.pop("fts_prior") + 0.10 * boost
            hits.append({**hit, "score": score})
        hits.sort(key=lambda hit: float(hit["score"]), reverse=True)
        return hits[:limit]
