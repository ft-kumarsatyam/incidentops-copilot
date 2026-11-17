from incidentops.agents.schemas import TriageContext
from incidentops.core.config import Settings
from incidentops.orchestration.merge import merge_partials
from incidentops.orchestration.rules_roles import change_role, impact_role, runbook_role


def test_merge_partials_dedupes_actions() -> None:
    partials = [
        impact_role(_context()).value,
        change_role(_context()).value,
        runbook_role(_context()).value,
    ]
    triage = merge_partials(partials, service="checkout-api", owner="oncall")
    types = [a.type for a in triage.actions]
    assert "open_issue" in types
    assert len(triage.actions) == len(set((a.type, a.description) for a in triage.actions))


def test_specialist_topology_provider(settings: Settings) -> None:
    from incidentops.agents.factory import build_provider

    provider = build_provider(settings.model_copy(update={"ai_topology": "specialists"}))
    assert provider.primary is not None
    assert provider.primary.name == "rolegraph:specialists"


def _context() -> TriageContext:
    return TriageContext(
        summary="checkout 5xx",
        service="checkout-api",
        owner="team",
        reporter="user",
        symptoms=["5xx"],
        monitoring={"error_rate_pct": 12},
        github={"recent_deploy": True, "repo": "org/checkout"},
        runbook_hits=[{"path": "checkout-api.md", "title": "Checkout", "excerpt": "x"}],
        missing_fields=[],
    )