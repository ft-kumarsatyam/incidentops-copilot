"""CHECKPOINT_DURABILITY=exit keeps the workflow correct and writes far fewer checkpoints."""

import sqlite3
from contextlib import closing

import pytest

from incidentops.core.config import Settings
from incidentops.workflows.incident import IncidentService
from tests.conftest import load_fixture


def _run(settings: Settings, durability: str) -> tuple[dict[str, object], int]:
    configured = settings.model_copy(update={"checkpoint_durability": durability})
    service = IncidentService(configured)
    try:
        started = service.start(load_fixture("incident_complete.json"))
        assert started["status"] == "awaiting_approval"
        final = service.decide(started["run_id"], started["idempotency_key"], "approved")
    finally:
        service.close()
    with closing(sqlite3.connect(configured.database_path)) as connection:
        writes = connection.execute("SELECT count(*) FROM writes").fetchone()[0]
        checkpoints = connection.execute("SELECT count(*) FROM checkpoints").fetchone()[0]
    return final, writes + checkpoints


@pytest.mark.parametrize("durability", ["sync", "async", "exit"])
def test_every_mode_completes_an_incident_with_an_approval(
    settings: Settings, durability: str
) -> None:
    final, _ = _run(settings, durability)

    assert final["status"] in {"succeeded", "failed"}
    assert final["approval"] == "approved"
    assert final["timeline"]


def test_exit_mode_writes_fewer_checkpoint_rows(settings: Settings, tmp_path) -> None:  # type: ignore[no-untyped-def]
    every_step = settings.model_copy(update={"database_path": tmp_path / "a.db"})
    only_at_exit = settings.model_copy(update={"database_path": tmp_path / "b.db"})

    _, rows_default = _run(every_step, "async")
    _, rows_exit = _run(only_at_exit, "exit")

    assert rows_exit < rows_default


def test_the_default_is_unchanged(settings: Settings) -> None:
    assert settings.checkpoint_durability == "async"
