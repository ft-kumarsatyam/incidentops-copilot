"""Structured slices produced by parallel specialist roles."""

from typing import Any

from pydantic import BaseModel, Field

from incidentops.agents.schemas import ProposedAction, Severity


class PartialOutcome(BaseModel):
    """One specialist's contribution before merge."""

    role_id: str
    severity: Severity | None = None
    confidence: float | None = None
    hypotheses: list[str] = Field(default_factory=list)
    actions: list[ProposedAction] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    notes: str = ""


class RoleInvocation(BaseModel):
    """Provenance for a single role call."""

    role_id: str
    provider: str
    input_tokens: int = 0
    output_tokens: int = 0
    extra: dict[str, Any] = Field(default_factory=dict)
