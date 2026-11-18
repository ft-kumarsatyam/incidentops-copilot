import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from incidentops.persistence import migrations
from incidentops.persistence.migrations import MIGRATIONS, Migration, migrate_sqlite
from incidentops.persistence.repo import IncidentRepo

ALL = [m.version for m in MIGRATIONS]


def _versions(path: Path) -> list[int]:
    with closing(sqlite3.connect(path)) as connection, connection:
        rows = connection.execute("SELECT version FROM schema_migrations ORDER BY version")
        return [row[0] for row in rows]


def test_a_fresh_database_records_every_migration(tmp_path: Path) -> None:
    IncidentRepo(tmp_path / "db.sqlite")

    assert _versions(tmp_path / "db.sqlite") == ALL


def test_reopening_applies_nothing_twice(tmp_path: Path) -> None:
    IncidentRepo(tmp_path / "db.sqlite")
    with closing(sqlite3.connect(tmp_path / "db.sqlite")) as connection, connection:
        assert migrate_sqlite(connection) == []


def test_a_database_from_before_migrations_is_adopted_without_losing_data(
    tmp_path: Path,
) -> None:
    path = tmp_path / "legacy.sqlite"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.executescript(MIGRATIONS[0].sqlite)  # the old unversioned schema
        connection.execute(
            "INSERT INTO incidents VALUES ('old', 'succeeded', NULL, 's', 'kept', '{}', 'c', 'u')"
        )

    repo = IncidentRepo(path)

    assert _versions(path) == ALL
    assert repo.search(query="kept")[1] == 1


def test_a_failing_migration_rolls_back_completely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    broken = Migration(
        len(MIGRATIONS) + 1, "broken", "CREATE TABLE half (x); SELECT * FROM missing_table;", ""
    )
    monkeypatch.setattr(migrations, "MIGRATIONS", (*MIGRATIONS, broken))
    path = tmp_path / "db.sqlite"
    with closing(sqlite3.connect(path)) as connection, connection:
        with pytest.raises(sqlite3.Error):
            migrate_sqlite(connection)
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }

    assert "half" not in tables
    assert _versions(path) == ALL


def test_version_gaps_are_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(migrations, "MIGRATIONS", (Migration(2, "skips one", "", ""),))

    with pytest.raises(RuntimeError, match="no gaps"):
        IncidentRepo(tmp_path / "db.sqlite")
