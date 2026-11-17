"""Claude-backed provider: structured outputs, bounded budgets, and typed failure modes."""

import json
import time
from importlib import resources
from typing import Any, Literal, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from incidentops.agents.schemas import (
    AIResult,
    CriticVerdict,
    ProviderError,
    Triage,
    TriageContext,
    Usage,
)
from incidentops.core.config import Settings
from incidentops.core.logging import get_logger
from incidentops.core.telemetry import span

log = get_logger(__name__)
M = TypeVar("M", bound=BaseModel)
Effort = Literal["low", "medium", "high"]


def _prompt(name: str) -> str:
    return resources.files("incidentops.agents.prompts").joinpath(name).read_text("utf-8")


def _untrusted_block(payload: dict[str, Any]) -> str:
    """Serialize incident data so it cannot close the <incident> tag early."""
    body = json.dumps(payload, sort_keys=True, default=str).replace("<", "\\u003c")
    return f"<incident>\n{body}\n</incident>"


class ClaudeProvider:
    def __init__(self, settings: Settings, client: anthropic.Anthropic | None = None) -> None:
        self.name = "claude"
        self._settings = settings
        self._client = client or anthropic.Anthropic(
            api_key=settings.anthropic_api_key,
            timeout=settings.ai_timeout_seconds,
            max_retries=2,
        )
        self._triage_prompt = _prompt("triage.md")
        self._critic_prompt = _prompt("critic.md")

    def triage(self, context: TriageContext) -> AIResult[Triage]:
        parsed, usage = self._call(
            "triage",
            self._settings.triage_model,
            self._settings.triage_effort,
            self._triage_prompt,
            context.to_payload(),
            Triage,
        )
        return AIResult(value=parsed, provider=f"claude:{self._settings.triage_model}", usage=usage)

    def review(self, context: TriageContext, triage: Triage) -> AIResult[CriticVerdict]:
        payload = {**context.to_payload(), "proposed_triage": triage.model_dump()}
        parsed, usage = self._call(
            "critic",
            self._settings.critic_model,
            self._settings.critic_effort,
            self._critic_prompt,
            payload,
            CriticVerdict,
        )
        return AIResult(value=parsed, provider=f"claude:{self._settings.critic_model}", usage=usage)

    def _call(
        self,
        role: str,
        model: str,
        effort: Effort,
        system: str,
        payload: dict[str, Any],
        schema: type[M],
    ) -> tuple[M, Usage]:
        started = time.perf_counter()
        try:
            with span(f"ai.{role}", model=model):
                response = self._client.messages.parse(
                    model=model,
                    max_tokens=self._settings.ai_max_output_tokens,
                    system=system,
                    messages=[{"role": "user", "content": _untrusted_block(payload)}],
                    output_format=schema,
                    output_config={"effort": effort},
                )
        except anthropic.APIError as error:
            raise ProviderError(f"{role}: API error ({type(error).__name__})") from error
        except (ValidationError, ValueError) as error:
            raise ProviderError(f"{role}: malformed model output") from error

        if response.stop_reason == "refusal":
            raise ProviderError(f"{role}: model declined the request")
        if response.stop_reason == "max_tokens":
            raise ProviderError(f"{role}: output truncated at max_tokens")
        parsed = response.parsed_output
        if parsed is None:
            raise ProviderError(f"{role}: no structured output returned")

        usage = Usage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cache_read_tokens=getattr(response.usage, "cache_read_input_tokens", 0) or 0,
        )
        log.info(
            "ai.call",
            role=role,
            model=model,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            latency_ms=round((time.perf_counter() - started) * 1000),
        )
        return parsed, usage
