"""The circuit breaker, and where it is applied (model provider, Jev, dispatch)."""

import threading
from typing import Any

import httpx
import pytest

from incidentops.agents.resilient import ResilientProvider
from incidentops.agents.schemas import ProviderError
from incidentops.core.config import Settings
from incidentops.core.resilience import CircuitBreaker
from incidentops.decision import JevClient, JevError
from tests.agents.helpers import ScriptedProvider, context, triage


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _breaker(clock: Clock, threshold: int = 3, reset: float = 30.0) -> CircuitBreaker:
    return CircuitBreaker("t", failure_threshold=threshold, reset_seconds=reset, clock=clock)


def test_it_opens_after_consecutive_failures_and_short_circuits() -> None:
    breaker = _breaker(Clock())
    for _ in range(3):
        assert breaker.allow()
        breaker.record_failure()

    assert breaker.state == "open"
    assert breaker.allow() is False
    assert breaker.snapshot()["short_circuited_total"] == 1
    assert breaker.opened_total == 1


def test_a_success_resets_the_count() -> None:
    breaker = _breaker(Clock())
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    breaker.record_failure()

    assert breaker.state == "closed"


def test_half_open_lets_exactly_one_trial_through() -> None:
    clock = Clock()
    breaker = _breaker(clock)
    for _ in range(3):
        breaker.record_failure()
    clock.now += 31

    assert breaker.state == "half_open"
    assert breaker.allow() is True  # the trial
    assert breaker.allow() is False  # everyone else still fails fast


def test_a_successful_trial_closes_and_a_failed_one_reopens() -> None:
    clock = Clock()
    breaker = _breaker(clock)
    for _ in range(3):
        breaker.record_failure()
    clock.now += 31
    breaker.allow()
    breaker.record_failure()

    assert breaker.state == "open" and breaker.opened_total == 2
    clock.now += 31
    breaker.allow()
    breaker.record_success()
    assert breaker.state == "closed" and breaker.allow()


def test_a_trial_that_never_reports_does_not_wedge_it() -> None:
    clock = Clock()
    breaker = _breaker(clock)
    for _ in range(3):
        breaker.record_failure()
    clock.now += 31
    assert breaker.allow() is True  # trial starts, then the caller crashes without reporting
    clock.now += 31

    assert breaker.allow() is True


def test_a_threshold_of_zero_disables_it() -> None:
    breaker = CircuitBreaker("off", failure_threshold=0)
    for _ in range(50):
        breaker.record_failure()

    assert breaker.allow() and breaker.state == "closed"


def test_it_is_safe_under_threads() -> None:
    breaker = CircuitBreaker("t", failure_threshold=1000, reset_seconds=60)

    def work() -> None:
        for _ in range(500):
            breaker.allow()
            breaker.record_failure()

    threads = [threading.Thread(target=work) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    assert breaker.opened_total == 1 and breaker.state == "open"


# -- model provider ---------------------------------------------------------------------------
def test_an_open_circuit_skips_the_model_and_uses_rules() -> None:
    primary = ScriptedProvider(ProviderError("triage: API error"))
    breaker = _breaker(Clock(), threshold=2)
    provider = ResilientProvider(primary, breaker=breaker)

    provider.triage(context())
    provider.triage(context())
    result = provider.triage(context())

    assert result.provider == "rules" and result.fallback_reason == "model circuit open"
    assert primary.triage_calls == 2  # the third call never reached the model


def test_a_guardrail_rejection_does_not_count_as_an_outage() -> None:
    hallucinated = triage(confidence=5)  # fails validation, but the model did answer
    breaker = _breaker(Clock(), threshold=2)
    provider = ResilientProvider(ScriptedProvider(hallucinated), breaker=breaker)

    for _ in range(5):
        provider.triage(context())

    assert breaker.state == "closed"


# -- Jev --------------------------------------------------------------------------------------
def _jev(handler: Any, **changes: Any) -> JevClient:
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        incidentops_env="dev",
        jev_enabled=True,
        jev_api_key="k",
        breaker_failure_threshold=2,
        **changes,
    )
    return JevClient(settings, transport=httpx.MockTransport(handler))


def test_jev_stops_being_called_after_repeated_server_errors() -> None:
    hits: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(1)
        return httpx.Response(503, json={})

    client = _jev(handler)
    question = {"q": {"type": "noul", "instructions": "x"}}
    for _ in range(2):
        with pytest.raises(JevError, match="HTTP 503"):
            client.evaluate("x", question)
    with pytest.raises(JevError, match="circuit open"):
        client.evaluate("x", question)

    assert len(hits) == 2 and client.breaker.state == "open"


def test_jev_client_errors_do_not_open_the_circuit() -> None:
    client = _jev(lambda request: httpx.Response(422, json={"detail": []}))
    for _ in range(5):
        with pytest.raises(JevError, match="422"):
            client.evaluate("x", {"q": {"type": "noul", "instructions": "x"}})

    assert client.breaker.state == "closed"


def test_jev_timeouts_open_the_circuit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    client = _jev(handler)
    for _ in range(2):
        with pytest.raises(JevError):
            client.evaluate("x", {"q": {"type": "noul", "instructions": "x"}})

    assert client.breaker.state == "open"
