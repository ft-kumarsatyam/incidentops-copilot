"""Typed contracts between the workflow and its agents.

Everything a model returns is parsed into these schemas, so downstream code never handles
free-form text. Actions are a closed set: a model can only *propose* from this list.
"""

from dataclasses import dataclass, field
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel

ActionType = Literal[
    "assign_commander",
    "notify",
    "page",
    "follow_runbook",
    "rollback",
    "scale",
    "open_issue",
]
Severity = Literal["SEV-1", "SEV-2", "SEV-3"]


class ProposedAction(BaseModel):
    type: ActionType
    description: str
    evidence: list[str]


class Triage(BaseModel):
    severity: Severity
    confidence: float
    hypotheses: list[str]
    actions: list[ProposedAction]
    conflicts: list[str]
    reasoning: str


class CriticVerdict(BaseModel):
    verdict: Literal["approve", "revise", "escalate"]
    issues: list[str]
    notes: str


@dataclass(frozen=True)
class TriageContext:
    """The sanitized incident facts an agent is allowed to see."""

    summary: str
    service: str
    owner: str
    reporter: str
    symptoms: list[str]
    monitoring: dict[str, Any]
    github: dict[str, Any]
    runbook_hits: list[dict[str, Any]]
    missing_fields: list[str]
    guardrail_flags: list[str] = field(default_factory=list)

    @property
    def evidence_refs(self) -> set[str]:
        """The only references an action may cite as its grounding."""
        refs = {"report"}
        if self.monitoring:
            refs.add("monitoring")
        if self.github:
            refs.add("github")
        refs.update(f"runbook:{hit['path']}" for hit in self.runbook_hits)
        return refs

    def to_payload(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "service": self.service,
            "owner": self.owner,
            "reporter": self.reporter,
            "symptoms": self.symptoms,
            "monitoring": self.monitoring,
            "github": self.github,
            "runbooks": [
                {
                    "ref": f"runbook:{h['path']}",
                    "title": h["title"],
                    "excerpt": h.get("excerpt", ""),
                }
                for h in self.runbook_hits
            ],
            "missing_fields": self.missing_fields,
            "guardrail_flags": self.guardrail_flags,
            "available_evidence": sorted(self.evidence_refs),
        }


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0


T = TypeVar("T")


@dataclass
class AIResult(Generic[T]):
    value: T
    provider: str
    usage: Usage = field(default_factory=Usage)
    fallback_reason: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


class ProviderError(RuntimeError):
    """The model call failed in a way the workflow should recover from by falling back."""


class GuardrailError(RuntimeError):
    """A model answer was syntactically valid but unsafe or ungrounded."""
