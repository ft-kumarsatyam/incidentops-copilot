from incidentops.agents.rules import RulesProvider
from incidentops.agents.specialists import SpecialistProvider, build_specialist_graph
from incidentops.workflows.incident import IncidentService
from tests.conftest import load_fixture


def test_specialist_subgraph_has_three_parallel_roles() -> None:
    nodes = set(build_specialist_graph().get_graph().nodes)

    assert {"role_impact", "role_change", "role_runbook", "merge"} <= nodes


def test_specialists_preserve_baseline_safety_and_output(settings) -> None:
    specialist_service = IncidentService(settings, provider=SpecialistProvider())
    baseline_service = IncidentService(
        settings.model_copy(update={"database_path": settings.database_path.with_name("rules.db")}),
        provider=RulesProvider(),
    )
    try:
        specialist = specialist_service.start(load_fixture("incident_complete.json"))
        baseline = baseline_service.start(load_fixture("incident_complete.json"))
    finally:
        specialist_service.close()
        baseline_service.close()

    assert specialist["severity"] == baseline["severity"] == "SEV-1"
    assert specialist["proposed_actions"] == baseline["proposed_actions"]
    assert specialist["ai"]["provider"] == "specialist-rules"
    assert specialist["status"] == "awaiting_approval"
