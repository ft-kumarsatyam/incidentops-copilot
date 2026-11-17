from pathlib import Path

import pytest

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
    from contextlib import closing

    database = tmp_path / "old.db"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE VIRTUAL TABLE runbook_chunks USING fts5(path, title, content)")
        # A matching row matters: the column-count mismatch only fails once a row is read.
        connection.execute(
            "INSERT INTO runbook_chunks VALUES ('old.md', 'Rollback', 'anything here at all')"
        )
        connection.commit()

    assert search_runbooks(database, "anything here") == []


def test_runbook_search_and_index_do_not_leak_connections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sqlite3

    opened: list[sqlite3.Connection] = []
    real_connect = sqlite3.connect

    def tracking_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        connection = real_connect(*args, **kwargs)  # type: ignore[arg-type]
        opened.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", tracking_connect)
    database = tmp_path / "rb.db"
    index_runbooks(_write(tmp_path / "runbooks"), database)
    search_runbooks(database, "sign-in outage")

    assert opened
    for connection in opened:
        with pytest.raises(sqlite3.ProgrammingError):  # closed connections reject use
            connection.execute("SELECT 1")
