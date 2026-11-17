"""Role specifications and handler protocol."""

from collections.abc import Callable
from dataclasses import dataclass

from incidentops.agents.schemas import AIResult, TriageContext
from incidentops.orchestration.schemas import PartialOutcome

RoleHandler = Callable[[TriageContext], AIResult[PartialOutcome]]


@dataclass(frozen=True)
class RoleSpec:
    id: str
    handler: RoleHandler