"""Collect per-role AI provenance for state and UI."""

from dataclasses import dataclass, field
from typing import Any

from incidentops.agents.schemas import AIResult, Usage
from incidentops.orchestration.schemas import RoleInvocation


@dataclass
class ProvenanceCollector:
    topology: str
    invocations: list[RoleInvocation] = field(default_factory=list)

    def record(self, role_id: str, result: AIResult[Any]) -> None:
        usage: Usage = result.usage
        self.invocations.append(
            RoleInvocation(
                role_id=role_id,
                provider=result.provider,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
            )
        )

    def to_ai_state(self, primary: AIResult[Any]) -> dict[str, object]:
        return {
            "provider": primary.provider,
            "fallback_reason": primary.fallback_reason,
            "input_tokens": primary.usage.input_tokens,
            "output_tokens": primary.usage.output_tokens,
            "topology": self.topology,
            "roles": [item.model_dump() for item in self.invocations],
        }
