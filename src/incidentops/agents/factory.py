"""Choose the provider from configuration."""

from incidentops.agents.resilient import ResilientProvider
from incidentops.core.config import Settings
from incidentops.core.logging import get_logger
from incidentops.orchestration.factory import RoleGraphProvider, wants_claude

log = get_logger(__name__)


def build_provider(settings: Settings) -> ResilientProvider:
    """``auto`` uses Claude when an API key is set; ``rules`` never calls a model."""
    if settings.ai_provider == "claude" and not settings.anthropic_api_key:
        raise RuntimeError("AI_PROVIDER=claude requires ANTHROPIC_API_KEY")
    primary: RoleGraphProvider | None = None
    if wants_claude(settings) or settings.ai_topology == "specialists":
        primary = RoleGraphProvider(settings)
        log.info(
            "ai.provider",
            provider=primary.name,
            topology=settings.ai_topology,
        )
        return ResilientProvider(primary)
    log.info("ai.provider", provider="rules")
    return ResilientProvider(None)
