import json
from pathlib import Path

import pytest

from incidentops import cli
from incidentops.core.config import Settings
from tests.conftest import FIXTURES


def test_check_reports_configuration(settings: Settings) -> None:
    report = cli.check(settings)

    assert report["environment"] == "dev"
    assert report["runbook_index"] == "present"
    assert report["n8n_actions"] == "not configured"


def test_run_fixture_with_approval(settings: Settings) -> None:
    state = cli.run_fixture(settings, FIXTURES / "incident_complete.json", "approved")

    assert state["status"] == "succeeded"


def test_run_fixture_without_approval_waits(settings: Settings) -> None:
    state = cli.run_fixture(settings, FIXTURES / "incident_complete.json", None)

    assert state["status"] == "awaiting_approval"


def test_main_prints_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "db.sqlite"))
    monkeypatch.setenv("LOG_LEVEL", "ERROR")
    monkeypatch.setattr("sys.argv", ["incidentops", "check"])

    cli.main()

    assert "database" in json.loads(capsys.readouterr().out)
