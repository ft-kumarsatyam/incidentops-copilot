"""Build TriageProvider from settings and RoleGraph topology."""

from incidentops.agents.schemas import AIResult, CriticVerdict, Triage, TriageContext
from incidentops.core.config import Settings
from incidentops.orchestration.registry import TopologyRegistry, register_default_topologies


class RoleGraphProvider:
    """TriageProvider implemented via TopologyRegistry."""

    def __init__(self, settings: Settings) -> None:
        register_default_topologies(settings)
        self._settings = settings
        self._topology = TopologyRegistry.get(settings.ai_topology)
        self.name = f"rolegraph:{settings.ai_topology}"

    def triage(self, context: TriageContext) -> AIResult[Triage]:
        assert self._topology.triage_fn is not None
        return self._topology.triage_fn(context)

    def review(self, context: TriageContext, triage: Triage) -> AIResult[CriticVerdict]:
        assert self._topology.review_fn is not None
        return self._topology.review_fn(context, triage)


def build_cognition_provider(settings: Settings) -> RoleGraphProvider:
    return RoleGraphProvider(settings)


def wants_claude(settings: Settings) -> bool:
    return settings.ai_provider == "claude" or (
        settings.ai_provider == "auto" and bool(settings.anthropic_api_key)
    )
