"""Optional Prometheus metrics. A no-op unless METRICS_ENABLED=true.

One registry per ``Metrics`` instance (not the global default), so several apps in one process
(tests, embedding) never collide. Labels are kept to small closed sets: the matched route
template, never the raw path, and never an incident id.
"""

from typing import Any

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


class Metrics:
    def __init__(self, enabled: bool = False, *, version: str = "unknown") -> None:
        self.enabled = enabled
        self._registry: Any = None
        if not enabled:
            return
        try:
            from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram
        except ImportError as error:
            raise RuntimeError(
                "METRICS_ENABLED=true needs the metrics extra: pip install 'incidentops[metrics]'"
            ) from error
        registry = CollectorRegistry()
        self._registry = registry
        self._requests = Counter(
            "incidentops_http_requests_total",
            "HTTP requests by method, matched route and status.",
            ["method", "route", "status"],
            registry=registry,
        )
        self._latency = Histogram(
            "incidentops_http_request_duration_seconds",
            "HTTP request latency by method and matched route.",
            ["method", "route"],
            registry=registry,
        )
        self._rate_limited = Counter(
            "incidentops_rate_limited_total",
            "Requests rejected by the rate limiter.",
            registry=registry,
        )
        self._incidents = Counter(
            "incidentops_incidents_total",
            "Incidents created, by the status the workflow reached and the proposed severity.",
            ["status", "severity"],
            registry=registry,
        )
        self._decisions = Counter(
            "incidentops_decisions_total",
            "Human decisions on incidents.",
            ["decision"],
            registry=registry,
        )
        self._dispatches = Counter(
            "incidentops_dispatch_total",
            "Approved-action dispatches by result.",
            ["result"],
            registry=registry,
        )
        Gauge(
            "incidentops_build_info",
            "Constant 1; the version is in the label.",
            ["version"],
            registry=registry,
        ).labels(version=version).set(1)

    def http(self, method: str, route: str, status: int, seconds: float) -> None:
        if not self.enabled:
            return
        self._requests.labels(method=method, route=route, status=str(status)).inc()
        self._latency.labels(method=method, route=route).observe(seconds)

    def rate_limited(self) -> None:
        if self.enabled:
            self._rate_limited.inc()

    def incident_created(self, status: str | None, severity: str | None) -> None:
        if self.enabled:
            self._incidents.labels(status=status or "unknown", severity=severity or "unknown").inc()

    def decision(self, decision: str) -> None:
        if self.enabled:
            self._decisions.labels(decision=decision).inc()

    def dispatch(self, result: str) -> None:
        if self.enabled:
            self._dispatches.labels(result=result).inc()

    def render(self) -> bytes:
        from prometheus_client import generate_latest

        return bytes(generate_latest(self._registry)) if self.enabled else b""
