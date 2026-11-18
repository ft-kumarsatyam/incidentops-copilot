"""An open live-event stream must not stop the server from shutting down.

Regression: with `make dev` (`--reload`), one browser tab holding /incidents/stream open made every
restart hang. uvicorn stops answering new requests, then waits for open connections to close, and
an SSE stream never does. From outside it looked like "publishing an incident takes 30 minutes":
requests were accepted and never answered.
"""

import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn

from incidentops.api.app import create_app
from incidentops.cli import serve_options
from incidentops.core.config import Settings

ROOT = Path(__file__).parents[2]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _serve(
    settings: Settings, graceful: int | None
) -> Iterator[tuple[uvicorn.Server, threading.Thread, int]]:
    port = _free_port()
    config = uvicorn.Config(
        create_app(settings),
        host="127.0.0.1",
        port=port,
        log_level="warning",
        timeout_graceful_shutdown=graceful,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started, "test server did not start"
    yield server, thread, port


def _open_stream(port: int) -> socket.socket:
    client = socket.create_connection(("127.0.0.1", port), timeout=5)
    client.sendall(
        b"GET /incidents/stream HTTP/1.1\r\nHost: x\r\nAccept: text/event-stream\r\n\r\n"
    )
    received = b""
    while b"event: ready" not in received:
        received += client.recv(4096)
    return client


def test_without_a_bound_an_open_stream_blocks_shutdown(settings: Settings) -> None:
    """The failure mode itself, so the fix below is known to be what fixes it."""
    (server, thread, port), client = next(gen := _serve(settings, None)), None
    try:
        client = _open_stream(port)
        server.should_exit = True
        thread.join(timeout=3)
        assert thread.is_alive(), "expected the open stream to hold the server up"
    finally:
        if client:
            client.close()  # the browser tab closing is what finally lets it stop
        server.should_exit = True
        thread.join(timeout=10)
        gen.close()
    assert not thread.is_alive()


def test_a_graceful_timeout_ends_the_stream_and_the_server_stops(settings: Settings) -> None:
    (server, thread, port), client = next(gen := _serve(settings, 1)), None
    try:
        client = _open_stream(port)
        started = time.monotonic()
        server.should_exit = True
        thread.join(timeout=8)
        assert not thread.is_alive(), "the server must stop even though the stream is still open"
        assert time.monotonic() - started < 6
    finally:
        if client:
            client.close()
        server.should_exit = True
        gen.close()


def test_serve_passes_the_bound_to_uvicorn(settings: Settings) -> None:
    options = serve_options(
        settings.model_copy(update={"shutdown_timeout_seconds": 7}),
        host="127.0.0.1",
        port=8000,
        reload=True,
    )
    assert options["timeout_graceful_shutdown"] == 7
    assert options["reload"] is True


def test_the_default_bound_is_finite(settings: Settings) -> None:
    assert 0 < settings.shutdown_timeout_seconds <= 30


@pytest.mark.parametrize("flag", ["--timeout-graceful-shutdown"])
def test_the_docker_command_is_bounded_too(flag: str) -> None:
    """A rolling restart of replicas hits the same hang as a dev reload."""
    assert flag in (ROOT / "Dockerfile").read_text(encoding="utf-8")
