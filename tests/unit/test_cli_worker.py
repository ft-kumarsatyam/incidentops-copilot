"""`incidentops worker` and `incidentops queue`: a separate worker process draining the queue."""

import json
import threading
import time
from pathlib import Path

import pytest

from incidentops import cli
from incidentops.core.config import Settings
from incidentops.workflows.incident import IncidentService
from tests.conftest import load_fixture

PAYLOAD = load_fixture("incident_complete.json")
RUN = PAYLOAD["run_id"]


def async_api(settings: Settings) -> Settings:
    """Settings for an API process that queues runs but runs no workers of its own."""
    return settings.model_copy(
        update={"execution_mode": "async", "worker_concurrency": 0, "worker_poll_seconds": 0.02}
    )


def test_a_separate_worker_process_runs_what_the_api_queued(settings: Settings) -> None:
    config = async_api(settings)
    api = IncidentService(config)
    stop = threading.Event()
    worker = threading.Thread(target=cli.run_worker, args=(config, 2, 5.0, stop))
    try:
        assert api.submit(PAYLOAD)["status"] == "queued"  # nobody is working yet
        time.sleep(0.2)
        assert api.get(RUN)["status"] == "queued"

        worker.start()  # the worker process comes up and drains the shared queue
        deadline = time.monotonic() + 10
        while api.get(RUN)["status"] != "awaiting_approval":
            assert time.monotonic() < deadline, "worker never ran the job"
            time.sleep(0.05)
    finally:
        stop.set()
        worker.join(15)
        api.close()

    assert not worker.is_alive(), "the worker did not shut down on request"


def test_the_worker_warns_when_its_events_cannot_reach_the_api(
    settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    stop = threading.Event()
    stop.set()  # start and stop straight away

    cli.run_worker(async_api(settings), 1, 1.0, stop)

    assert "REDIS_URL" in capsys.readouterr().out


def test_queue_commands_inspect_and_repair(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    settings: Settings,
) -> None:
    database = tmp_path / "q.sqlite"
    monkeypatch.setenv("DATABASE_PATH", str(database))
    monkeypatch.setenv("LOG_LEVEL", "ERROR")
    monkeypatch.setenv("EXECUTION_MODE", "async")
    monkeypatch.setenv("INCIDENTOPS_ENV", "dev")
    service = IncidentService(settings.model_copy(update={"database_path": database}))
    service.submit(PAYLOAD)
    service.close()
    capsys.readouterr()  # drop what setting the queue up printed; we parse only the CLI output

    monkeypatch.setattr("sys.argv", ["incidentops", "queue", "stats"])
    cli.main()
    stats = json.loads(capsys.readouterr().out)
    assert stats["queued"] == 1 and stats["dead"] == 0

    monkeypatch.setattr("sys.argv", ["incidentops", "queue", "retry", RUN])
    with pytest.raises(Exception, match="failed analysis"):  # it is queued, not errored
        cli.main()
