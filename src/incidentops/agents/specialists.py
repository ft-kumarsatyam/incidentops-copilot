"""Specialist topology provider for architecture benchmarks (RoleGraph-backed)."""

from typing import Any

from incidentops.agents.rules import RulesProvider
from incidentops.agents.schemas import AIResult, CriticVerdict, Triage, TriageContext
from incidentops.core.config import Settings
from incidentops.orchestration.parallel import build_parallel_stage
from incidentops.orchestration.registry import SpecialistCognitionProvider
from incidentops.orchestration.roles import RoleSpec
from incidentops.orchestration.rules_roles import change_role, impact_role, runbook_role


def build_specialist_graph() -> Any:
    """Compatibility shim: expose parallel role node ids for tests."""
    roles = [
        RoleSpec(id="impact", handler=impact_role),
        RoleSpec(id="change", handler=change_role),
        RoleSpec(id="runbook", handler=runbook_role),
    ]
    return build_parallel_stage(roles, topology_id="specialists")


class SpecialistProvider:
    """Triage via RoleGraph parallel specialists; review via the shared rules critic."""

    name = "specialist-rules"

    def __init__(self) -> None:
        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            ai_provider="rules",
            ai_topology="specialists",
        )
        self._inner = SpecialistCognitionProvider(settings, use_llm=False)
        self._critic = RulesProvider()

    def triage(self, context: TriageContext) -> AIResult[Triage]:
        return self._inner.triage(context)

    def review(self, context: TriageContext, triage: Triage) -> AIResult[CriticVerdict]:
        result = self._critic.review(context, triage)
        result.provider = self.name
        return result
