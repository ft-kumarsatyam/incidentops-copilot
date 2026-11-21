"""Compose a model provider with the deterministic fallback."""

from typing import Protocol

from incidentops.agents.guardrails import validate_triage, validate_verdict
from incidentops.agents.rules import RulesProvider
from incidentops.agents.schemas import (
    AIResult,
    CriticVerdict,
    GuardrailError,
    ProviderError,
    Triage,
    TriageContext,
)
from incidentops.core.logging import get_logger
from incidentops.core.resilience import CircuitBreaker

log = get_logger(__name__)


class TriageProvider(Protocol):
    name: str

    def triage(self, context: TriageContext) -> AIResult[Triage]: ...

    def review(self, context: TriageContext, triage: Triage) -> AIResult[CriticVerdict]: ...


class ResilientProvider:
    """Try the model, validate its answer, and fall back to rules on any failure.

    The workflow therefore always gets a valid answer, and the state records which provider
    produced it and why a fallback happened.
    """

    def __init__(
        self,
        primary: TriageProvider | None,
        fallback: TriageProvider | None = None,
        breaker: CircuitBreaker | None = None,
    ):
        self.primary = primary
        self.breaker = breaker
        self.fallback: TriageProvider = fallback or RulesProvider()
        self.name = primary.name if primary else self.fallback.name

    def triage(self, context: TriageContext) -> AIResult[Triage]:
        reason: str | None = None
        if self.primary is not None:
            if self.breaker is not None and not self.breaker.allow():
                reason = "model circuit open"
                log.warning("ai.fallback", role="triage", reason=reason)
            else:
                try:
                    result = self.primary.triage(context)
                    self._reachable()
                    result.value = validate_triage(result.value, context)
                    return result
                except ProviderError as error:
                    self._unreachable()
                    reason = str(error)
                    log.warning("ai.fallback", role="triage", reason=reason)
                except GuardrailError as error:
                    reason = str(error)
                    log.warning("ai.fallback", role="triage", reason=reason)
        fallback = self.fallback.triage(context)
        fallback.fallback_reason = reason
        return fallback

    def review(self, context: TriageContext, triage: Triage) -> AIResult[CriticVerdict]:
        reason: str | None = None
        if self.primary is not None:
            if self.breaker is not None and not self.breaker.allow():
                reason = "model circuit open"
                log.warning("ai.fallback", role="critic", reason=reason)
            else:
                try:
                    result = self.primary.review(context, triage)
                    self._reachable()
                    result.value = validate_verdict(result.value)
                    return result
                except ProviderError as error:
                    self._unreachable()
                    reason = str(error)
                    log.warning("ai.fallback", role="critic", reason=reason)
                except GuardrailError as error:
                    reason = str(error)
                    log.warning("ai.fallback", role="critic", reason=reason)
        fallback = self.fallback.review(context, triage)
        fallback.fallback_reason = reason
        return fallback

    # A guardrail rejection means the model answered, so it still counts as reachable: only a
    # provider error (timeout, API error, refusal, malformed output) counts towards opening.
    def _reachable(self) -> None:
        if self.breaker is not None:
            self.breaker.record_success()

    def _unreachable(self) -> None:
        if self.breaker is not None:
            self.breaker.record_failure()
