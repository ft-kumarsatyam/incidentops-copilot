import pytest

from incidentops.agents.factory import build_provider
from incidentops.core.config import Settings


def test_auto_without_a_key_uses_rules_only(settings: Settings) -> None:
    provider = build_provider(settings.model_copy(update={"ai_provider": "auto"}))

    assert provider.primary is None
    assert provider.name == "rules"


def test_rules_mode_never_builds_a_model_client(settings: Settings) -> None:
    keyed = settings.model_copy(update={"ai_provider": "rules", "anthropic_api_key": "sk-x"})

    assert build_provider(keyed).primary is None


def test_auto_with_a_key_uses_claude(settings: Settings) -> None:
    keyed = settings.model_copy(update={"ai_provider": "auto", "anthropic_api_key": "sk-test"})

    provider = build_provider(keyed)

    assert provider.primary is not None
    assert provider.name == "claude"


def test_claude_mode_without_a_key_is_a_startup_error(settings: Settings) -> None:
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        build_provider(settings.model_copy(update={"ai_provider": "claude"}))
