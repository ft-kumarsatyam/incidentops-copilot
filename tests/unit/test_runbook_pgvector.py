"""The shared Postgres runbook index: full-text search, pgvector hybrid, safe reindexing."""

import re
import shutil
import threading
from collections.abc import Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from incidentops.api.app import create_app
from incidentops.core.config import Settings
from incidentops.rag import (
    PostgresRetriever,
    build_runbook_retriever,
    index_runbooks_for_settings,
    index_runbooks_postgres,
    runbook_index_status,
    search_runbooks,
)
from incidentops.rag.fts import index_runbooks, load_chunks
from incidentops.workflows.incident import IncidentService
from tests.conftest import ROOT, load_fixture, require_module, unavailable

RUNBOOKS = ROOT / "runbooks"
DIMS = 384
TOPICS = {
    0: {
        "zzlogin",
        "login",
        "oauth",
        "signin",
        "sign",
        "auth",
        "authentication",
        "token",
        "session",
    },
    1: {"zzcheckout", "checkout", "payment", "purchase", "purchases", "cart", "5xx"},
}


def topic_embedder(texts: Sequence[str]) -> list[list[float]]:
    """A deterministic stand-in for a model: one dimension per topic, no download needed."""
    vectors = []
    for text in texts:
        words = set(re.findall(r"[a-z0-9]+", text.lower()))
        vector = [0.0] * DIMS
        for dim, keys in TOPICS.items():
            vector[dim] = float(len(words & keys))
        if not any(vector):
            vector[DIMS - 1] = 1.0
        norm = sum(x * x for x in vector) ** 0.5
        vectors.append([x / norm for x in vector])
    return vectors


@pytest.fixture
def pgvector_url(pg_url: str) -> str:
    """A throwaway database that has the pgvector extension, or a skip/fail like other services."""
    require_module("psycopg")
    import psycopg

    try:
        with psycopg.connect(pg_url, autocommit=True) as connection:
            connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
    except psycopg.errors.FeatureNotSupported:
        unavailable("this Postgres has no pgvector (use the pgvector/pgvector:pg17 image)")
    return pg_url


def test_lexical_index_finds_the_right_runbook(pg_url: str) -> None:
    index_runbooks_postgres(RUNBOOKS, pg_url)
    retriever = PostgresRetriever(pg_url)

    deploy = retriever.search("checkout 5xx after deploy", 3)
    assert deploy[0]["path"] == "checkout-api.md"
    assert "[" in deploy[0]["excerpt"]  # the matched terms are highlighted
    assert retriever.search("authentication sign-in outage", 3)[0]["path"] == "auth-api.md"


def test_the_preferred_service_ranks_first(pg_url: str) -> None:
    index_runbooks_postgres(RUNBOOKS, pg_url)
    retriever = PostgresRetriever(pg_url)
    query = "safe mitigation verification"

    assert retriever.search(query, 3, prefer="auth-api")[0]["path"] == "auth-api.md"
    assert retriever.search(query, 3, prefer="checkout-api")[0]["path"] == "checkout-api.md"


@pytest.mark.parametrize(
    ("query", "limit"), [("", 3), ("!! ??", 3), ("checkout", 0), ("zzzqqq", 3)]
)
def test_nothing_to_search_for_returns_nothing(pg_url: str, query: str, limit: int) -> None:
    index_runbooks_postgres(RUNBOOKS, pg_url)
    assert PostgresRetriever(pg_url).search(query, limit) == []


def test_searching_before_the_index_is_built_returns_nothing(pg_url: str) -> None:
    assert PostgresRetriever(pg_url).search("checkout", 3) == []


def test_status_reports_missing_until_the_index_is_built(pg_url: str, settings: Settings) -> None:
    configured = settings.model_copy(update={"runbook_store": "postgres", "database_url": pg_url})
    assert runbook_index_status(configured) == "missing"
    index_runbooks_for_settings(configured)
    assert runbook_index_status(configured) == "ok"


def test_an_unreachable_store_is_unavailable_not_a_crash(settings: Settings) -> None:
    require_module("psycopg")
    broken = settings.model_copy(
        update={"runbook_store": "postgres", "database_url": "postgresql://x:y@127.0.0.1:1/z"}
    )
    assert runbook_index_status(broken) == "unavailable"


def test_the_postgres_store_requires_a_database_url(settings: Settings) -> None:
    configured = settings.model_copy(update={"runbook_store": "postgres", "database_url": None})
    with pytest.raises(ValueError, match="DATABASE_URL"):
        build_runbook_retriever(configured)
    with pytest.raises(ValueError, match="DATABASE_URL"):
        index_runbooks_for_settings(configured)


def test_reindexing_unchanged_runbooks_is_a_no_op(pg_url: str) -> None:
    first = index_runbooks_postgres(RUNBOOKS, pg_url)
    second = index_runbooks_postgres(RUNBOOKS, pg_url)

    assert first["skipped"] == 0
    assert second == {**second, "skipped": 1, "indexed_chunks": first["indexed_chunks"]}
    assert index_runbooks_postgres(RUNBOOKS, pg_url, force=True)["skipped"] == 0


def test_an_edited_runbook_is_picked_up(pg_url: str, tmp_path: Path) -> None:
    corpus = tmp_path / "runbooks"
    shutil.copytree(RUNBOOKS, corpus)
    index_runbooks_postgres(corpus, pg_url)
    assert PostgresRetriever(pg_url).search("quokka", 3) == []

    (corpus / "checkout-api.md").write_text(
        (corpus / "checkout-api.md").read_text() + "\n## Quokka\nFeed the quokka.\n"
    )
    result = index_runbooks_postgres(corpus, pg_url)

    assert result["skipped"] == 0
    assert PostgresRetriever(pg_url).search("quokka", 3)[0]["title"] == "Quokka"


def test_replicas_reindexing_at_the_same_time_leave_one_consistent_index(pg_url: str) -> None:
    import psycopg

    errors: list[BaseException] = []
    results: list[dict[str, int]] = []

    def worker() -> None:
        try:
            results.append(index_runbooks_postgres(RUNBOOKS, pg_url))
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    with psycopg.connect(pg_url) as connection:
        count = connection.execute("SELECT count(*) FROM runbook_chunks").fetchone()
        metas = connection.execute("SELECT count(*) FROM runbook_index_meta").fetchone()
    assert count == (len(load_chunks(RUNBOOKS)),)
    assert metas == (1,)
    assert any(r["skipped"] == 0 for r in results)  # somebody did the work
    assert any(r["skipped"] == 1 for r in results)  # and the rest did not redo it


def test_hybrid_finds_a_runbook_that_shares_no_words_with_the_query(pgvector_url: str) -> None:
    """The reranking-only SQLite hybrid cannot do this: it never sees a chunk FTS missed."""
    index_runbooks_postgres(RUNBOOKS, pgvector_url, embeddings=True, embedder=topic_embedder)
    query = "zzlogin"

    assert PostgresRetriever(pgvector_url).search(query, 3) == []  # lexical alone: nothing
    hybrid = PostgresRetriever(pgvector_url, hybrid=True, embedder=topic_embedder)
    hits = hybrid.search(query, 3)

    assert hits and hits[0]["path"] == "auth-api.md"
    assert hits[0]["dense_score"] > 0.5


def test_hybrid_combines_lexical_and_dense_signals(pgvector_url: str) -> None:
    index_runbooks_postgres(RUNBOOKS, pgvector_url, embeddings=True, embedder=topic_embedder)
    hybrid = PostgresRetriever(pgvector_url, hybrid=True, embedder=topic_embedder)

    hits = hybrid.search("checkout 5xx after deploy", 3)

    assert hits[0]["path"] == "checkout-api.md"
    assert all(0.0 <= float(h["score"]) <= 1.1 for h in hits)
    assert [h["score"] for h in hits] == sorted((h["score"] for h in hits), reverse=True)


def test_switching_to_hybrid_rebuilds_the_index_with_vectors(pgvector_url: str) -> None:
    import psycopg

    first = index_runbooks_postgres(RUNBOOKS, pgvector_url)
    second = index_runbooks_postgres(
        RUNBOOKS, pgvector_url, embeddings=True, embedder=topic_embedder
    )

    assert first["indexed_embeddings"] == 0
    assert second["skipped"] == 0
    assert second["indexed_embeddings"] == second["indexed_chunks"] > 0
    with psycopg.connect(pgvector_url) as connection:
        row = connection.execute(
            "SELECT count(*) FROM runbook_chunks WHERE embedding IS NOT NULL"
        ).fetchone()
    assert row == (second["indexed_chunks"],)


def test_real_embeddings_rescue_the_semantic_cases_lexical_search_misses(
    pgvector_url: str, tmp_path: Path
) -> None:
    """The retrieval eval's own 'semantic' queries, raw: no rewrite, no preferred service."""
    require_module("fastembed")
    lexical_db = tmp_path / "lexical.db"
    index_runbooks(RUNBOOKS, lexical_db)
    index_runbooks_postgres(RUNBOOKS, pgvector_url, embeddings=True)
    hybrid = PostgresRetriever(pgvector_url, hybrid=True)

    cases = {
        "login broken OAuth": "auth-api.md",
        "users cannot complete purchases": "checkout-api.md",
    }
    for query, expected in cases.items():
        assert hybrid.search(query, 3)[0]["path"] == expected, query
    lexical_misses = [
        q for q, p in cases.items()
        if p not in {h["path"] for h in search_runbooks(lexical_db, q, 3)}
    ]  # fmt: skip
    assert lexical_misses, "expected plain lexical search to miss at least one of these"


def test_an_incident_uses_the_shared_index_end_to_end(pg_url: str, settings: Settings) -> None:
    configured = settings.model_copy(
        update={"runbook_store": "postgres", "database_url": pg_url, "runbook_retrieval": "fts"}
    )
    index_runbooks_for_settings(configured)
    service = IncidentService(configured)
    try:
        state = service.start(load_fixture("incident_complete.json"))
    finally:
        service.close()

    retrieved = [line for line in state["timeline"] if line.startswith("retrieved ")]
    assert retrieved and not retrieved[0].startswith("retrieved 0 ")
    assert any("checkout-api.md" in str(e) for e in state.get("evidence", []))


def test_ready_reports_the_shared_index(pg_url: str, settings: Settings) -> None:
    configured = settings.model_copy(
        update={
            "runbook_store": "postgres",
            "database_url": pg_url,
            "auto_index_runbooks": True,
            "incidentops_api_key": "k",
        }
    )
    with TestClient(create_app(configured)) as client:
        body = client.get("/ready").json()
    assert body["checks"]["runbook_index"] == "ok"
    assert body["status"] == "ready"
