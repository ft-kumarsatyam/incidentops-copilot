"""Structured logging. JSON in production, readable console output in development."""

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import structlog


def configure_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level.upper(), force=True)
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level.upper())),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str = "incidentops") -> Any:
    return structlog.get_logger(name)


@contextmanager
def bound_context(**values: Any) -> Iterator[None]:
    """Attach key/value pairs (request_id, run_id, ...) to every log line in the block."""
    tokens = structlog.contextvars.bind_contextvars(**values)
    try:
        yield
    finally:
        structlog.contextvars.reset_contextvars(**tokens)
