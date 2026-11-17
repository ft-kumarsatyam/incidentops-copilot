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

    def __init__(self, primary: TriageProvider | None, fallback: TriageProvider | None = None):
        self.primary = primary
        self.fallback: TriageProvider = fallback or RulesProvider()
        self.name = primary.name if primary else self.fallback.name

    def triage(self, context: TriageContext) -> AIResult[Triage]:
        reason: str | None = None
        if self.primary is not None:
            try:
                result = self.primary.triage(context)
                result.value = validate_triage(result.value, context)
                return result
            except (ProviderError, GuardrailError) as error:
                reason = str(error)
                log.warning("ai.fallback", role="triage", reason=reason)
        fallback = self.fallback.triage(context)
        fallback.fallback_reason = reason
        return fallback

    def review(self, context: TriageContext, triage: Triage) -> AIResult[CriticVerdict]:
        reason: str | None = None
        if self.primary is not None:
            try:
                result = self.primary.review(context, triage)
                result.value = validate_verdict(result.value)
                return result
            except (ProviderError, GuardrailError) as error:
                reason = str(error)
                log.warning("ai.fallback", role="critic", reason=reason)
        fallback = self.fallback.review(context, triage)
        fallback.fallback_reason = reason
        return fallback
