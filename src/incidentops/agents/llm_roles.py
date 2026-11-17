"""LLM-backed specialist role handlers (partial outcomes)."""

from importlib import resources
from typing import Any, Literal, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from incidentops.agents.schemas import (
    AIResult,
    ProposedAction,
    ProviderError,
    Severity,
    TriageContext,
    Usage,
)
from incidentops.core.config import Settings
from incidentops.core.logging import get_logger
from incidentops.orchestration.schemas import PartialOutcome

log = get_logger(__name__)
M = TypeVar("M", bound=BaseModel)
Effort = Literal["low", "medium", "high"]


class ImpactPartial(BaseModel):
    severity: Severity
    confidence: float
    conflicts: list[str] = []
    notes: str = ""


class ChangePartial(BaseModel):
    hypotheses: list[str] = []
    actions: list[ProposedAction] = []
    notes: str = ""


class RunbookPartial(BaseModel):
    actions: list[ProposedAction] = []
    notes: str = ""


def _prompt(name: str) -> str:
    return (
        resources.files("incidentops.agents.prompts")
        .joinpath("specialists", name)
        .read_text("utf-8")
    )


def _untrusted_block(payload: dict[str, Any]) -> str:
    import json

    body = json.dumps(payload, sort_keys=True, default=str).replace("<", "\\u003c")
    return f"<incident>\n{body}\n</incident>"


class SpecialistCaller:
    def __init__(self, settings: Settings, client: anthropic.Anthropic | None = None) -> None:
        self._settings = settings
        self._client = client or anthropic.Anthropic(
            api_key=settings.anthropic_api_key,
            timeout=settings.ai_timeout_seconds,
            max_retries=2,
        )
        self._prompts = {
            "impact": _prompt("impact.md"),
            "change": _prompt("change.md"),
            "runbook": _prompt("runbook.md"),
        }

    def call(self, role: str, context: TriageContext, schema: type[M]) -> AIResult[PartialOutcome]:
        model = self._settings.specialist_model or self._settings.triage_model
        try:
            response = self._client.messages.parse(
                model=model,
                max_tokens=self._settings.ai_max_output_tokens,
                system=self._prompts[role],
                messages=[{"role": "user", "content": _untrusted_block(context.to_payload())}],
                output_format=schema,
                output_config={"effort": self._settings.triage_effort},
            )
        except anthropic.APIError as error:
            raise ProviderError(f"{role}: API error ({type(error).__name__})") from error
        except (ValidationError, ValueError) as error:
            raise ProviderError(f"{role}: malformed model output") from error

        if response.stop_reason in ("refusal", "max_tokens"):
            raise ProviderError(f"{role}: stop_reason={response.stop_reason}")
        parsed = response.parsed_output
        if parsed is None:
            raise ProviderError(f"{role}: no structured output")

        usage = Usage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
        partial = _to_partial(role, parsed)
        log.info(
            "ai.call",
            role=f"specialist:{role}",
            model=model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
        )
        return AIResult(
            value=partial,
            provider=f"claude:{model}:{role}",
            usage=usage,
        )


def _to_partial(role: str, parsed: BaseModel) -> PartialOutcome:
    if role == "impact" and isinstance(parsed, ImpactPartial):
        return PartialOutcome(
            role_id="impact",
            severity=parsed.severity,
            confidence=parsed.confidence,
            conflicts=parsed.conflicts,
            notes=parsed.notes,
        )
    if role == "change" and isinstance(parsed, ChangePartial):
        return PartialOutcome(
            role_id="change",
            hypotheses=parsed.hypotheses,
            actions=parsed.actions,
            notes=parsed.notes,
        )
    if role == "runbook" and isinstance(parsed, RunbookPartial):
        return PartialOutcome(role_id="runbook", actions=parsed.actions, notes=parsed.notes)
    raise ProviderError(f"{role}: unexpected schema")


def build_llm_specialist_handlers(settings: Settings) -> dict[str, Any]:
    caller = SpecialistCaller(settings)

    def impact(context: TriageContext) -> AIResult[PartialOutcome]:
        return caller.call("impact", context, ImpactPartial)

    def change(context: TriageContext) -> AIResult[PartialOutcome]:
        return caller.call("change", context, ChangePartial)

    def runbook(context: TriageContext) -> AIResult[PartialOutcome]:
        return caller.call("runbook", context, RunbookPartial)

    return {"impact": impact, "change": change, "runbook": runbook}
