import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from incidentops.core.config import Settings
from incidentops.tools.runbooks import index_runbooks
from incidentops.workflows.incident import IncidentService

ROOT = Path(__file__).parents[1]
FIXTURES = Path(__file__).parent / "fixtures"


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
