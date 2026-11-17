"""Optional OpenTelemetry tracing. A no-op unless OTEL_EXPORTER_OTLP_ENDPOINT is set."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

_tracer: Any = None
_initialised = False


def _init() -> None:
    global _tracer, _initialised
    _initialised = True
    if not os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT"):
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:  # extras not installed: stay a no-op
        return
    provider = TracerProvider(resource=Resource.create({"service.name": "incidentops"}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer("incidentops")


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[None]:
    if not _initialised:
        _init()
    if _tracer is None:
        yield
        return
    with _tracer.start_as_current_span(name) as active:
        for key, value in attributes.items():
            active.set_attribute(key, value)
        yield
