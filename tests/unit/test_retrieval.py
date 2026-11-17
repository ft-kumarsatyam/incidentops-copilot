from pathlib import Path

import pytest

from incidentops.core.config import Settings
from incidentops.rag import FtsRetriever, build_runbook_retriever, index_runbooks_for_settings


def test_build_runbook_retriever_defaults_to_fts(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        runbook_db=tmp_path / "rb.db",
        runbook_dir=tmp_path / "missing",
        runbook_retrieval="fts",
    )
    assert isinstance(build_runbook_retriever(settings), FtsRetriever)


def test_index_runbooks_for_settings_fts(tmp_path: Path) -> None:
    runbooks = tmp_path / "runbooks"
    runbooks.mkdir()
    (runbooks / "svc.md").write_text("# Svc\n\n## A\n\nhello world", encoding="utf-8")
    db = tmp_path / "rb.db"
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        runbook_dir=runbooks,
        runbook_db=db,
        runbook_retrieval="fts",
    )
    stats = index_runbooks_for_settings(settings)
    assert stats["indexed_chunks"] >= 1
    hits = FtsRetriever(db).search("hello", 3)
    assert hits[0]["path"] == "svc.md"


def test_hybrid_index_and_search(tmp_path: Path) -> None:
    pytest.importorskip("fastembed")
    runbooks = tmp_path / "runbooks"
    runbooks.mkdir()
    (runbooks / "checkout-api.md").write_text(
        "# Checkout\n\n## Pay\n\npayment gateway errors\n",
        encoding="utf-8",
    )
    db = tmp_path / "rb.db"
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        runbook_dir=runbooks,
        runbook_db=db,
        runbook_retrieval="hybrid",
    )
    index_runbooks_for_settings(settings)
    hits = build_runbook_retriever(settings).search(
        "users cannot complete purchases",
        3,
        prefer="checkout-api",
    )
    assert hits and hits[0]["path"] == "checkout-api.md"
