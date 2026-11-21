"""A small circuit breaker for outbound dependencies (the model, Jev, the action webhook).

Without one, a dependency that is down makes every call wait out its full timeout (and retries),
so one outage slows every request that touches it. The breaker counts consecutive failures; once
``failure_threshold`` is reached it *opens* and calls fail at once for ``reset_seconds``. Then it
lets one trial call through (half-open): success closes it, failure opens it again.

Only failures that say "the dependency is unhealthy" should be recorded (timeouts, connection
errors, 5xx, 429). A bad request or a rejected key is the caller's problem, not an outage.
"""

import threading
import time
from collections.abc import Callable
from typing import Literal

from incidentops.core.logging import get_logger

log = get_logger(__name__)

State = Literal["closed", "open", "half_open"]


class CircuitOpenError(RuntimeError):
    """The breaker is open: the call was not attempted."""

    def __init__(self, name: str) -> None:
        super().__init__(f"circuit open for {name}")
        self.name = name


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 5,
        reset_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_seconds = reset_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at: float | None = None
        self._trial_in_flight = False
        self._trial_at = 0.0
        self.opened_total = 0
        self.short_circuited_total = 0

    @property
    def enabled(self) -> bool:
        return self.failure_threshold > 0

    @property
    def state(self) -> State:
        with self._lock:
            return self._state()

    def _state(self) -> State:
        if self._opened_at is None:
            return "closed"
        if self._clock() - self._opened_at >= self.reset_seconds:
            return "half_open"
        return "open"

    def allow(self) -> bool:
        """True if a call may go ahead. In half-open state exactly one caller gets True."""
        if not self.enabled:
            return True
        with self._lock:
            state = self._state()
            if state == "closed":
                return True
            # A trial whose outcome was never recorded (an unexpected error) must not wedge the
            # breaker: after another reset period a new trial is allowed.
            stale = self._clock() - self._trial_at >= self.reset_seconds
            if state == "half_open" and (not self._trial_in_flight or stale):
                self._trial_in_flight = True
                self._trial_at = self._clock()
                return True
            self.short_circuited_total += 1
            return False

    def record_success(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            if self._opened_at is not None:
                log.info("breaker.closed", name=self.name)
            self._failures = 0
            self._opened_at = None
            self._trial_in_flight = False

    def record_failure(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            was_trial = self._trial_in_flight
            self._trial_in_flight = False
            self._failures += 1
            reached = self._opened_at is None and self._failures >= self.failure_threshold
            if was_trial or reached:
                self.opened_total += 1
                log.warning("breaker.opened", name=self.name, failures=self._failures)
                self._opened_at = self._clock()

    def snapshot(self) -> dict[str, object]:
        return {
            "name": self.name,
            "state": self.state,
            "opened_total": self.opened_total,
            "short_circuited_total": self.short_circuited_total,
        }
