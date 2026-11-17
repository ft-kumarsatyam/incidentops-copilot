import json
from pathlib import Path


def test_n8n_workflow_has_ingress_approval_and_idempotency() -> None:
    workflow = json.loads(
        (Path(__file__).parents[1] / "n8n" / "incident-ops.json").read_text(encoding="utf-8")
    )
    nodes = {node["name"]: node for node in workflow["nodes"]}

    assert nodes["Slack Incident Ingress"]["parameters"]["path"] == "incidentops/intake"
    assert nodes["Approval Callback"]["parameters"]["path"] == "incidentops/decision"
    assert nodes["Approval Callback"]["parameters"]["httpMethod"] == "GET"
    gate = nodes["Approval and Idempotency Gate"]["parameters"]["jsCode"]
    assert "$getWorkflowStaticData" in gate
    assert "idempotency_key required" in gate
    issue = nodes["Create GitHub Incident"]["parameters"]
    assert "Idempotency-Key" in json.dumps(issue)
    assert "Resume LangGraph" in nodes
    assert "X-API-Key" in json.dumps(nodes["Run LangGraph"])
    assert nodes["Approved Action Dispatch"]["parameters"]["path"] == "incidentops/actions"
    assert "action:" in nodes["Action Idempotency Gate"]["parameters"]["jsCode"]
