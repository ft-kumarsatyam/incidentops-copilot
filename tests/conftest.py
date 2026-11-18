import importlib
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NoReturn

import pytest

from incidentops.core.config import Settings
from incidentops.rag import index_runbooks
from incidentops.workflows.incident import IncidentService

ROOT = Path(__file__).parents[1]
FIXTURES = Path(__file__).parent / "fixtures"


def unavailable(reason: str) -> NoReturn:
    """A test needs something this machine lacks (a service, an optional extra).

    Normally that skips the test. With TESTS_STRICT=1 (set by `make test`, `make cov`, `make
    check` and CI) it fails instead, so a missing dependency can never hide as a quiet skip.
    """
    if os.environ.get("TESTS_STRICT"):
        pytest.fail(f"TESTS_STRICT is set but: {reason}", pytrace=False)
    pytest.skip(reason)


def require_module(name: str) -> None:
    try:
        importlib.import_module(name)
    except ImportError:
        unavailable(f"optional dependency {name!r} is not installed")


def load_fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    runbook_db = tmp_path / "runbooks.db"
    index_runbooks(ROOT / "runbooks", runbook_db)
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        incidentops_env="dev",
        ai_provider="rules",  # tests never call a real model, even if a key is set in the env
        anthropic_api_key=None,
        database_path=tmp_path / "incidentops.db",
        runbook_dir=ROOT / "runbooks",
        runbook_db=runbook_db,
        static_dir=tmp_path / "no-ui",  # tests must not depend on whether the UI was built
        rate_limit_per_minute=0,
        n8n_dispatch_backoff=0,
        log_json=False,
        log_level="WARNING",
    )


@pytest.fixture
def service(settings: Settings) -> Iterator[IncidentService]:
    instance = IncidentService(settings)
    yield instance
    instance.close()


@pytest.fixture
def pg_url() -> Iterator[str]:
    """A fresh, empty Postgres database per test. Skipped unless TEST_DATABASE_URL is set.

    TEST_DATABASE_URL points at a server (any database on it); each test gets its own throwaway
    database, so nothing in the named database is ever touched.
    """
    import uuid

    admin_url = os.environ.get("TEST_DATABASE_URL")
    if not admin_url:
        unavailable("set TEST_DATABASE_URL to run Postgres tests (make test starts one)")
    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    name = f"incidentops_t_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield make_conninfo(**{**conninfo_to_dict(admin_url), "dbname": name})
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture
def redis_url() -> str:
    """URL of a Redis server for the shared-state tests. Skipped unless TEST_REDIS_URL is set."""
    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        unavailable("set TEST_REDIS_URL to run Redis tests (make test starts one)")
    return url


@pytest.fixture
def redis_prefix() -> str:
    """A unique key prefix per test, so tests sharing one Redis never see each other's data."""
    import uuid

    return f"t{uuid.uuid4().hex[:10]}"
