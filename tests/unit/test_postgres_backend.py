"""Postgres-only behaviour: migrations under concurrency, and the full service on Postgres."""

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import psycopg
import pytest

from incidentops.core.config import Settings
from incidentops.persistence.migrations import MIGRATIONS, migrate_postgres
from incidentops.workflows.incident import IncidentService
from tests.conftest import load_fixture


def _applied(url: str) -> list[int]:
    with psycopg.connect(url) as connection:
        rows = connection.execute("SELECT version FROM schema_migrations ORDER BY version")
        return [row[0] for row in rows.fetchall()]


def test_migrations_are_idempotent(pg_url: str) -> None:
    with psycopg.connect(pg_url, autocommit=True) as connection:
        first = migrate_postgres(connection)
        second = migrate_postgres(connection)

    assert first == [m.version for m in MIGRATIONS]
    assert second == []


def test_replicas_starting_together_apply_each_migration_once(pg_url: str) -> None:
    def start(_: int) -> list[int]:
        with psycopg.connect(pg_url, autocommit=True) as connection:
            return migrate_postgres(connection)

    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(start, range(6)))

    assert sorted(v for ran in results for v in ran) == [m.version for m in MIGRATIONS]
    assert _applied(pg_url) == [m.version for m in MIGRATIONS]


def test_an_unreachable_database_fails_fast_at_startup(settings: Settings) -> None:
    unreachable = settings.model_copy(
        update={"database_url": "postgresql://nobody@127.0.0.1:1/none?connect_timeout=2"}
    )

    with pytest.raises(psycopg.OperationalError):
        IncidentService(unreachable)


def test_a_run_survives_a_service_restart_on_postgres(settings: Settings, pg_url: str) -> None:
    configured = settings.model_copy(update={"database_url": pg_url})
    payload = load_fixture("incident_complete.json")
    first = IncidentService(configured)
    started = first.start(payload)
    first.close()

    second = IncidentService(configured)  # new process, same database
    try:
        decided = second.decide(payload["run_id"], started["idempotency_key"], "approved")
        audit = [entry["event"] for entry in second.audit(payload["run_id"])]
    finally:
        second.close()

    assert started["status"] == "awaiting_approval"
    assert decided["status"] == "succeeded"
    assert decided["timeline"].count("incident received") == 1
    assert audit == ["created", "decision:approved", "dispatch:not_configured"]


def test_two_services_share_one_database_and_one_winner_decides(
    settings: Settings, pg_url: str
) -> None:
    configured = settings.model_copy(update={"database_url": pg_url})
    payload = load_fixture("incident_complete.json")
    replica_a, replica_b = IncidentService(configured), IncidentService(configured)
    try:
        started = replica_a.start(payload)
        seen_by_b = replica_b.get(payload["run_id"])
        outcomes: list[Any] = []

        def decide(service: IncidentService) -> None:
            try:
                outcomes.append(
                    service.decide(payload["run_id"], started["idempotency_key"], "approved")
                )
            except Exception as error:
                outcomes.append(error)

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(decide, [replica_a, replica_b]))
    finally:
        replica_a.close()
        replica_b.close()

    assert seen_by_b["status"] == "awaiting_approval"
    winners = [o for o in outcomes if isinstance(o, dict)]
    assert len(winners) == 1 and winners[0]["status"] == "succeeded"
