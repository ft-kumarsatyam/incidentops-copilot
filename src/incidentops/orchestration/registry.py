"""Topology registration for RoleGraph."""

from incidentops.agents.resilient import TriageProvider
from incidentops.agents.rules import RulesProvider
from incidentops.agents.schemas import AIResult, CriticVerdict, Triage, TriageContext
from incidentops.core.config import Settings
from incidentops.orchestration.parallel import build_parallel_stage, invoke_parallel_stage
from incidentops.orchestration.roles import RoleSpec
from incidentops.orchestration.rules_roles import change_role, impact_role, runbook_role
from incidentops.orchestration.topology import CognitionTopology


class TopologyRegistry:
    _topologies: dict[str, CognitionTopology] = {}

    @classmethod
    def register(cls, topology: CognitionTopology) -> None:
        cls._topologies[topology.id] = topology

    @classmethod
    def get(cls, topology_id: str) -> CognitionTopology:
        if topology_id not in cls._topologies:
            raise KeyError(f"unknown ai_topology: {topology_id}")
        return cls._topologies[topology_id]


def _specialist_roles(use_llm: bool, settings: Settings) -> list[RoleSpec]:
    if use_llm:
        from incidentops.agents.llm_roles import build_llm_specialist_handlers

        handlers = build_llm_specialist_handlers(settings)
        return [
            RoleSpec(id="impact", handler=handlers["impact"]),
            RoleSpec(id="change", handler=handlers["change"]),
            RoleSpec(id="runbook", handler=handlers["runbook"]),
        ]
    return [
        RoleSpec(id="impact", handler=impact_role),
        RoleSpec(id="change", handler=change_role),
        RoleSpec(id="runbook", handler=runbook_role),
    ]


class SpecialistCognitionProvider:
    """Parallel specialists compiled via RoleGraph."""

    def __init__(self, settings: Settings, *, use_llm: bool) -> None:
        roles = _specialist_roles(use_llm, settings)
        self.name = "specialist-llm" if use_llm else "specialist-rules"
        self._topology_id = "specialists"
        self._compiled = build_parallel_stage(roles, topology_id=self._topology_id)
        critic: TriageProvider = RulesProvider()
        if use_llm:
            from incidentops.agents.claude import ClaudeProvider

            critic = ClaudeProvider(settings)
        self._critic = critic

    def triage(self, context: TriageContext) -> AIResult[Triage]:
        from incidentops.agents.schemas import Usage

        triage, collector = invoke_parallel_stage(
            self._compiled, context, topology_id=self._topology_id
        )
        total_in = sum(r.input_tokens for r in collector.invocations)
        total_out = sum(r.output_tokens for r in collector.invocations)
        result = AIResult(
            value=triage,
            provider=self.name,
            usage=Usage(input_tokens=total_in, output_tokens=total_out),
        )
        result.meta["ai"] = collector.to_ai_state(result)
        return result

    def review(self, context: TriageContext, triage: Triage) -> AIResult[CriticVerdict]:
        review = self._critic.review(context, triage)
        review.provider = self.name
        return review


def register_default_topologies(settings: Settings) -> None:
    TopologyRegistry._topologies.clear()
    use_claude = settings.ai_provider == "claude" or (
        settings.ai_provider == "auto" and bool(settings.anthropic_api_key)
    )
    use_llm_specialists = use_claude and settings.ai_topology == "specialists"

    def monolithic_triage(context: TriageContext) -> AIResult[Triage]:
        if use_claude:
            from incidentops.agents.claude import ClaudeProvider

            return ClaudeProvider(settings).triage(context)
        return RulesProvider().triage(context)

    def monolithic_review(
        context: TriageContext, triage: Triage
    ) -> AIResult[CriticVerdict]:
        if use_claude:
            from incidentops.agents.claude import ClaudeProvider

            return ClaudeProvider(settings).review(context, triage)
        return RulesProvider().review(context, triage)

    TopologyRegistry.register(
        CognitionTopology(
            id="monolithic",
            parallel=False,
            triage_fn=monolithic_triage,
            review_fn=monolithic_review,
        )
    )

    specialist_provider = SpecialistCognitionProvider(
        settings, use_llm=use_llm_specialists
    )

    def specialist_triage(context: TriageContext) -> AIResult[Triage]:
        return specialist_provider.triage(context)

    def specialist_review(
        context: TriageContext, triage: Triage
    ) -> AIResult[CriticVerdict]:
        return specialist_provider.review(context, triage)

    TopologyRegistry.register(
        CognitionTopology(
            id="specialists",
            parallel=True,
            triage_fn=specialist_triage,
            review_fn=specialist_review,
        )
    )