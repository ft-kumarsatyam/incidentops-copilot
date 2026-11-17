import json
from pathlib import Path


def test_n8n_workflow_has_ingress_approval_and_idempotency() -> None:
    workflow = json.loads(
        (Path(__file__).parents[1] / "n8n" / "incident-ops.json").read_text(encoding="utf-8")
    )
    nodes = {node["name"]: node for node in workflow["nodes"]}

    assert nodes["Slack Incident Ingress"]["parameters"]["path"] == "incidentops/intake"
    assert nodes["Approval Callback"]["parameters"]["path"] == "incidentops/decision"
    gate = nodes["Approval and Idempotency Gate"]["parameters"]["jsCode"]
    assert "$getWorkflowStaticData" in gate
    assert "idempotency_key required" in gate
    issue = nodes["Create GitHub Incident"]["parameters"]
    assert "Idempotency-Key" in json.dumps(issue)
