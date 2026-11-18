"""The load generator measures what it claims to, against a real in-process API."""

import asyncio
import importlib.util
import sys

import httpx
import pytest

from incidentops.api.app import create_app
from incidentops.core.config import Settings
from tests.conftest import ROOT

_spec = importlib.util.spec_from_file_location("loadtest", ROOT / "scripts" / "loadtest.py")
assert _spec and _spec.loader
loadtest = importlib.util.module_from_spec(_spec)
sys.modules["loadtest"] = loadtest
_spec.loader.exec_module(loadtest)


@pytest.mark.parametrize(
    ("values", "fraction", "expected"),
    [([], 0.95, 0.0), ([5.0], 0.99, 5.0), (list(map(float, range(1, 101))), 0.95, 95.0),
     (list(map(float, range(1, 101))), 0.50, 50.0), ([1.0, 2.0, 3.0, 4.0], 0.99, 4.0)],
)  # fmt: skip
def test_percentile_is_nearest_rank(values: list[float], fraction: float, expected: float) -> None:
    assert loadtest.percentile(values, fraction) == expected


def test_each_generated_incident_is_distinct_and_valid() -> None:
    first, second = loadtest.incident_payload(1, 1), loadtest.incident_payload(1, 2)
    assert first["run_id"] != second["run_id"]
    assert first["summary"] != second["summary"]


def test_summarize_counts_errors_and_computes_rates() -> None:
    samples = loadtest.Samples()
    samples.latencies["incident"] += [10.0, 20.0, 30.0, 40.0]
    samples.fail("create", "boom")
    summary = loadtest.summarize(samples, 2.0)

    assert summary["incidents"] == 4
    assert summary["incidents_per_second"] == 2.0
    assert summary["errors"] == 1 and summary["first_error"] == "boom"
    assert summary["incident"]["p50"] == 20.0


def test_a_run_creates_and_approves_incidents_across_replicas(settings: Settings) -> None:
    """Two in-process 'replicas' sharing one database: the approval crosses to the other one."""
    shared = settings.model_copy(
        update={"incidentops_api_key": "k", "n8n_action_webhook_url": None}
    )
    apps = [create_app(shared), create_app(shared)]
    transports = [httpx.ASGITransport(app=app) for app in apps]

    async def go() -> dict[str, object]:
        async with apps[0].router.lifespan_context(apps[0]):
            async with apps[1].router.lifespan_context(apps[1]):
                return await loadtest.run_load(
                    ["http://replica-a", "http://replica-b"],
                    users=3,
                    seconds=1.5,
                    warmup=0.2,
                    api_key="k",
                    transports=transports,
                )

    result = asyncio.run(go())

    assert result["errors"] == 0, result["first_error"]
    assert result["incidents"] > 0
    assert result["replicas"] == 2
    for operation in ("create", "decide", "incident"):
        assert result[operation]["p95"] > 0  # type: ignore[index]


def test_a_failing_server_is_reported_as_errors_not_swallowed() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    result = asyncio.run(
        loadtest.run_load(
            ["http://x"],
            users=2,
            seconds=0.5,
            warmup=0.0,
            transports=[httpx.MockTransport(refuse)],
        )
    )

    assert result["errors"] > 0 and "503" in result["first_error"]
    assert result["incidents"] == 0


def test_the_report_states_the_scaling_it_measured() -> None:
    def cell(replicas: int, rate: float, p95: float) -> dict[str, object]:
        stats = {"p50": p95 / 2, "p95": p95, "p99": p95 * 1.2, "max": p95 * 2}
        return {
            "replicas": replicas, "users": 16, "incidents": 100, "errors": 0,
            "incidents_per_second": rate, "incident": stats, "create": stats, "decide": stats,
            "db_commits_per_incident": 37.0, "db_inserts_per_incident": 100.0,
            "postgres_cpu_percent": 180,
        }  # fmt: skip

    text = loadtest.render(
        [cell(1, 50.0, 300.0), cell(4, 75.0, 250.0)],
        hardware="test box",
        seconds=30,
        versions="Python x",
        docker_cpus=2,
    )

    assert "50.0 → 75.0 incidents/s (×1.50)" in text
    assert "180% CPU" in text and "2 cores" in text
    assert "one incident costs Postgres about 37" in text
    assert "| async | 4 | 16 | 75.0 |" in text


@pytest.mark.parametrize(
    ("wait_type", "event", "query", "label"),
    [
        (None, None, "SELECT 1", None),
        ("Client", "ClientRead", "SELECT 1", "waiting for the API (idle between statements)"),
        ("Lock", "tuple", "SELECT seq FROM audit_heads WHERE tenant_id = $1 FOR UPDATE",
         "audit chain head row lock"),
        ("Lock", "transactionid", "SELECT seq FROM audit_heads FOR UPDATE",
         "audit chain head row lock"),
        ("IO", "WalSync", "INSERT INTO checkpoints", "WAL flush (commit durability)"),
        ("LWLock", "WALWrite", "COMMIT", "WAL flush (commit durability)"),
        ("Lock", "transactionid", "UPDATE incidents SET", "other row locks"),
    ],
)  # fmt: skip
def test_database_waits_get_a_readable_label(
    wait_type: str | None, event: str | None, query: str, label: str | None
) -> None:
    assert loadtest.wait_category(wait_type, event, query) == label


def test_the_report_names_where_the_database_waited() -> None:
    stats = {"p50": 1.0, "p95": 2.0, "p99": 3.0, "max": 4.0}
    cell = {
        "replicas": 4, "users": 64, "incidents": 10, "errors": 0, "incidents_per_second": 60.0,
        "incident": stats, "create": stats, "decide": stats,
        "db_waits": {"audit chain head row lock": 75, "WAL flush (commit durability)": 25,
                     "waiting for the API (idle between statements)": 500},
    }  # fmt: skip
    text = loadtest.render([cell], hardware="x", seconds=30, versions="y", docker_cpus=2)

    assert "audit chain head row lock 75%, WAL flush (commit durability) 25%" in text
