from types import SimpleNamespace
from typing import Any

from incidentops.agents.schemas import (
    AIResult,
    CriticVerdict,
    ProposedAction,
    ProviderError,
    Triage,
    TriageContext,
    Usage,
)


def context(**overrides: Any) -> TriageContext:
    base: dict[str, Any] = {
        "summary": "Checkout API is returning elevated 5xx responses",
        "service": "checkout-api",
        "owner": "payments-oncall",
        "reporter": "U-ONCALL",
        "symptoms": ["12% error rate"],
        "monitoring": {"service": "checkout-api", "error_rate": 0.12},
        "github": {"repo": "acme/checkout", "recent_deploy": "abc123"},
        "runbook_hits": [{"path": "checkout-api.md", "title": "Rollback", "excerpt": "..."}],
        "missing_fields": [],
    }
    base.update(overrides)
    return TriageContext(**base)


def action(kind: str = "notify", evidence: list[str] | None = None, text: str = "Do it") -> Any:
    return ProposedAction(type=kind, description=text, evidence=evidence or ["report"])  # type: ignore[arg-type]


def triage(**overrides: Any) -> Triage:
    base: dict[str, Any] = {
        "severity": "SEV-1",
        "confidence": 0.9,
        "hypotheses": ["bad deploy"],
        "actions": [action()],
        "conflicts": [],
        "reasoning": "because",
    }
    base.update(overrides)
    return Triage(**base)


class ScriptedProvider:
    """A provider whose answers (or failures) are scripted, for deterministic tests."""

    name = "scripted"

    def __init__(
        self,
        triage_result: Triage | Exception | None = None,
        verdict: CriticVerdict | Exception | None = None,
    ) -> None:
        self.triage_result = triage_result
        self.verdict = verdict or CriticVerdict(verdict="approve", issues=[], notes="")
        self.triage_calls = 0
        self.review_calls = 0

    def triage(self, ctx: TriageContext) -> AIResult[Triage]:
        self.triage_calls += 1
        result = self.triage_result or triage()
        if isinstance(result, Exception):
            raise result
        return AIResult(value=result, provider="scripted", usage=Usage(11, 7))

    def review(self, ctx: TriageContext, proposal: Triage) -> AIResult[CriticVerdict]:
        self.review_calls += 1
        if isinstance(self.verdict, Exception):
            raise self.verdict
        return AIResult(value=self.verdict, provider="scripted")


class FakeMessages:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = outcomes
        self.calls: list[dict[str, Any]] = []

    def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeClient:
    def __init__(self, outcomes: list[Any]) -> None:
        self.messages = FakeMessages(outcomes)


def model_response(parsed: Any, stop_reason: str = "end_turn") -> Any:
    usage = SimpleNamespace(input_tokens=120, output_tokens=45, cache_read_input_tokens=0)
    return SimpleNamespace(parsed_output=parsed, stop_reason=stop_reason, usage=usage)


__all__ = ["ProviderError"]
