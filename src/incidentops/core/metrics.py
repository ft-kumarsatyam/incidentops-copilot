"""Optional Prometheus metrics. A no-op unless METRICS_ENABLED=true.

One registry per ``Metrics`` instance (not the global default), so several apps in one process
(tests, embedding) never collide. Labels are kept to small closed sets: the matched route
template, never the raw path, and never an incident id.
"""

from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from incidentops.core.resilience import CircuitBreaker

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
        self._breakers: list[CircuitBreaker] = []
        self._jev: list[Callable[[], dict[str, float]]] = []
        registry.register(_DependencyCollector(self._breakers, self._jev))
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

    def watch_breaker(self, breaker: "CircuitBreaker | None") -> None:
        """Export a breaker's state and counters, read at scrape time."""
        if self.enabled and breaker is not None:
            self._breakers.append(breaker)

    def watch_jev(self, read: Callable[[], dict[str, float]]) -> None:
        """Export Jev call, error and token counters (``read`` returns their current values)."""
        if self.enabled:
            self._jev.append(read)

    def render(self) -> bytes:
        from prometheus_client import generate_latest

        return bytes(generate_latest(self._registry)) if self.enabled else b""


_STATE_VALUE = {"closed": 0, "half_open": 1, "open": 2}


class _DependencyCollector:
    """Reads breaker and Jev counters when Prometheus scrapes, so they need no update hooks."""

    def __init__(self, breakers: "list[CircuitBreaker]", jev: list[Callable[[], dict[str, float]]]):
        self._breakers = breakers
        self._jev = jev

    def collect(self) -> Iterator[Any]:
        from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily

        state = GaugeMetricFamily(
            "incidentops_circuit_state",
            "Circuit breaker state per dependency: 0 closed, 1 half-open, 2 open.",
            labels=["name"],
        )
        opened = CounterMetricFamily(
            "incidentops_circuit_opened",
            "Times a dependency's circuit breaker opened.",
            labels=["name"],
        )
        skipped = CounterMetricFamily(
            "incidentops_circuit_short_circuited",
            "Calls refused at once because a circuit was open.",
            labels=["name"],
        )
        for breaker in self._breakers:
            state.add_metric([breaker.name], _STATE_VALUE[breaker.state])
            opened.add_metric([breaker.name], breaker.opened_total)
            skipped.add_metric([breaker.name], breaker.short_circuited_total)
        yield state
        yield opened
        yield skipped

        calls = CounterMetricFamily("incidentops_jev_calls", "Calls made to Jev.")
        errors = CounterMetricFamily("incidentops_jev_errors", "Jev calls that failed.")
        tokens = CounterMetricFamily(
            "incidentops_jev_tokens", "Jev tokens by direction.", labels=["direction"]
        )
        totals = {"calls": 0.0, "errors": 0.0, "input_tokens": 0.0, "output_tokens": 0.0}
        for read in self._jev:
            for key, value in read().items():
                totals[key] += value
        calls.add_metric([], totals["calls"])
        errors.add_metric([], totals["errors"])
        tokens.add_metric(["input"], totals["input_tokens"])
        tokens.add_metric(["output"], totals["output_tokens"])
        yield calls
        yield errors
        yield tokens
