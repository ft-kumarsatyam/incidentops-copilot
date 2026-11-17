from incidentops.agents.guardrails import validate_triage
from incidentops.agents.rules import RulesProvider
from tests.agents.helpers import context


def test_rules_output_passes_its_own_guardrails() -> None:
    ctx = context()
    result = RulesProvider().triage(ctx)

    assert validate_triage(result.value, ctx).actions == result.value.actions


def test_severity_follows_error_rate() -> None:
    rules = RulesProvider()

    assert rules.triage(context()).value.severity == "SEV-1"
    mid = context(monitoring={"error_rate": 0.05}, symptoms=[])
    assert rules.triage(mid).value.severity == "SEV-2"
    low = context(monitoring={"error_rate": 0.01}, symptoms=[])
    assert rules.triage(low).value.severity == "SEV-3"


def test_runbook_and_rollback_actions_are_grounded_in_evidence() -> None:
    actions = {a.type: a for a in RulesProvider().triage(context()).value.actions}

    assert actions["follow_runbook"].evidence == ["runbook:checkout-api.md"]
    assert actions["rollback"].evidence == ["github"]


def test_monitoring_timeout_lowers_confidence() -> None:
    normal = RulesProvider().triage(context()).value.confidence
    timed_out = (
        RulesProvider()
        .triage(context(monitoring={"error_rate": 0.12, "timed_out": True}))
        .value.confidence
    )

    assert timed_out < normal
