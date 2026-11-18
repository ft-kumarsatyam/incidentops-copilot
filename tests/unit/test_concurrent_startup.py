"""Replicas that start at the same moment on an empty database must all come up."""

import threading

import pytest

from incidentops.core.config import Settings
from incidentops.persistence.factory import open_storage
from tests.conftest import require_module


@pytest.mark.parametrize("attempt", range(3))
def test_replicas_starting_together_on_an_empty_database_all_open(
    pg_url: str, settings: Settings, attempt: int
) -> None:
    """LangGraph's checkpoint setup is not safe to run concurrently, so it runs under a lock."""
    require_module("psycopg")
    configured = settings.model_copy(update={"database_url": pg_url, "database_pool_size": 2})
    errors: list[BaseException] = []
    opened = []
    barrier = threading.Barrier(6)

    def start() -> None:
        try:
            barrier.wait()
            opened.append(open_storage(configured))
        except BaseException as error:  # noqa: BLE001
            errors.append(error)

    threads = [threading.Thread(target=start) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    for storage in opened:
        storage.close()

    assert errors == []
    assert len(opened) == 6
