"""Named cognitive topologies."""

from collections.abc import Callable
from dataclasses import dataclass, field

from incidentops.agents.schemas import AIResult, CriticVerdict, Triage, TriageContext
from incidentops.orchestration.roles import RoleSpec


@dataclass(frozen=True)
class CognitionTopology:
    id: str
    parallel: bool
    roles: list[RoleSpec] = field(default_factory=list)
    triage_fn: Callable[[TriageContext], AIResult[Triage]] | None = None
    review_fn: Callable[[TriageContext, Triage], AIResult[CriticVerdict]] | None = None
