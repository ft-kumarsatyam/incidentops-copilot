"""Choose the provider from configuration."""

from incidentops.agents.resilient import ResilientProvider
from incidentops.core.config import Settings
from incidentops.core.logging import get_logger

log = get_logger(__name__)


def build_provider(settings: Settings) -> ResilientProvider:
    """``auto`` uses Claude when an API key is set; ``rules`` never calls a model."""
    wants_claude = settings.ai_provider == "claude" or (
        settings.ai_provider == "auto" and bool(settings.anthropic_api_key)
    )
    if not wants_claude:
        log.info("ai.provider", provider="rules")
        return ResilientProvider(None)
    if not settings.anthropic_api_key:
        raise RuntimeError("AI_PROVIDER=claude requires ANTHROPIC_API_KEY")
    from incidentops.agents.claude import ClaudeProvider

    log.info("ai.provider", provider="claude", triage=settings.triage_model)
    return ResilientProvider(ClaudeProvider(settings))
