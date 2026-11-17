import pytest

from incidentops.agents.guardrails import validate_triage
from incidentops.agents.schemas import GuardrailError
from tests.agents.helpers import action, context, triage


def test_keeps_grounded_actions_and_drops_fabricated_references() -> None:
    proposal = triage(
        actions=[
            action("notify", ["report"], "Post an update"),
            action("page", ["invented:source"], "Page the CEO"),
        ]
    )

    result = validate_triage(proposal, context())

    assert [item.description for item in result.actions] == ["Post an update"]


def test_rollback_requires_a_deploy_to_roll_back() -> None:
    proposal = triage(
        actions=[action("rollback", ["github"], "Roll back"), action("notify", ["report"], "Tell")]
    )

    result = validate_triage(proposal, context(github={"repo": "acme/x"}))

    assert [item.type for item in result.actions] == ["notify"]


def test_runbook_action_must_cite_a_runbook() -> None:
    proposal = triage(
        actions=[
            action("follow_runbook", ["report"], "Follow the runbook"),
            action("follow_runbook", ["runbook:checkout-api.md"], "Follow checkout runbook"),
        ]
    )

    result = validate_triage(proposal, context())

    assert [item.description for item in result.actions] == ["Follow checkout runbook"]


def test_raises_when_nothing_is_grounded() -> None:
    proposal = triage(actions=[action("page", ["made-up"], "Page someone")])

    with pytest.raises(GuardrailError, match="grounded"):
        validate_triage(proposal, context())


def test_clamps_confidence_dedupes_and_bounds_text() -> None:
    proposal = triage(
        confidence=7.0,
        hypotheses=[f"h{i}" for i in range(20)],
        actions=[
            action("notify", ["report"], "x" * 1000),
            action("notify", ["report"], "x" * 1000),
        ],
    )

    result = validate_triage(proposal, context())

    assert result.confidence == 1.0
    assert len(result.hypotheses) == 5
    assert len(result.actions) == 1
    assert len(result.actions[0].description) == 300


def test_flags_a_severity_that_contradicts_measured_impact() -> None:
    result = validate_triage(triage(severity="SEV-3"), context())  # 12% error rate

    assert any("conflicts with measured impact" in item for item in result.conflicts)


def test_no_severity_conflict_when_monitoring_is_unverified() -> None:
    monitoring = {"error_rate": 0.12, "timed_out": True}
    result = validate_triage(triage(severity="SEV-3"), context(monitoring=monitoring))

    assert result.conflicts == []
