from incidentops.agents.resilient import ResilientProvider
from incidentops.agents.schemas import CriticVerdict, ProviderError
from tests.agents.helpers import ScriptedProvider, action, context, triage


def test_valid_model_answer_is_used_and_validated() -> None:
    provider = ResilientProvider(ScriptedProvider(triage(confidence=5)))

    result = provider.triage(context())

    assert result.provider == "scripted"
    assert result.fallback_reason is None
    assert result.value.confidence == 1.0


def test_provider_failure_falls_back_to_rules_and_records_why() -> None:
    provider = ResilientProvider(ScriptedProvider(ProviderError("triage: API error")))

    result = provider.triage(context())

    assert result.provider == "rules"
    assert result.fallback_reason == "triage: API error"
    assert result.value.actions  # the workflow still gets a usable proposal


def test_ungrounded_model_answer_falls_back_to_rules() -> None:
    hallucinated = triage(actions=[action("page", ["nowhere"], "Page the VP")])
    provider = ResilientProvider(ScriptedProvider(hallucinated))

    result = provider.triage(context())

    assert result.provider == "rules"
    assert "grounded" in (result.fallback_reason or "")


def test_critic_failure_falls_back_to_rules_review() -> None:
    primary = ScriptedProvider(verdict=ProviderError("critic: timeout"))
    provider = ResilientProvider(primary)

    result = provider.review(context(), triage())

    assert result.provider == "rules"
    assert result.fallback_reason == "critic: timeout"


def test_no_primary_means_rules_only() -> None:
    provider = ResilientProvider(None)

    assert provider.name == "rules"
    assert provider.triage(context()).provider == "rules"
    assert provider.review(context(missing_fields=["owner"]), triage()).value == CriticVerdict(
        verdict="revise", issues=["missing owner"], notes="Rule-based review."
    )
