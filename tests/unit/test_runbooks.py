from pathlib import Path

from incidentops.tools.runbooks import index_runbooks, search_runbooks


def _write(directory: Path) -> Path:
    directory.mkdir()
    (directory / "auth-api.md").write_text(
        "# Authentication API incident runbook\n\n## Sign-in outage\n\nCheck the IdP.",
        encoding="utf-8",
    )
    (directory / "checkout-api.md").write_text(
        "# Checkout API incident runbook\n\n## Sign-in failures\n\nCheck the payment gateway.",
        encoding="utf-8",
    )
    return directory


def test_document_title_terms_match_chunks(tmp_path: Path) -> None:
    database = tmp_path / "rb.db"
    index_runbooks(_write(tmp_path / "runbooks"), database)

    hits = search_runbooks(database, "authentication outage")

    assert hits[0]["path"] == "auth-api.md"


def test_prefer_ranks_the_services_own_runbook_first(tmp_path: Path) -> None:
    database = tmp_path / "rb.db"
    index_runbooks(_write(tmp_path / "runbooks"), database)

    plain = search_runbooks(database, "sign-in failures api")
    preferred = search_runbooks(database, "sign-in failures api", prefer="auth-api")

    assert plain[0]["path"] == "checkout-api.md"
    assert preferred[0]["path"] == "auth-api.md"


def test_old_index_schema_degrades_to_no_results(tmp_path: Path) -> None:
    import sqlite3

    database = tmp_path / "old.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE VIRTUAL TABLE runbook_chunks USING fts5(path, title, content)")

    assert search_runbooks(database, "anything here") == []
