import json
import shutil
import subprocess
import textwrap
from pathlib import Path
from typing import Any

import pytest

WORKFLOW = Path(__file__).parents[2] / "n8n" / "incident-ops.json"


def _workflow() -> dict[str, Any]:
    workflow: dict[str, Any] = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    return workflow


def _nodes() -> dict[str, Any]:
    return {node["name"]: node for node in _workflow()["nodes"]}


def test_workflow_has_intake_and_actions_webhooks_only() -> None:
    webhooks = {
        name: node["parameters"]
        for name, node in _nodes().items()
        if node["type"] == "n8n-nodes-base.webhook"
    }

    assert {w["path"] for w in webhooks.values()} == {
        "incidentops/intake",
        "incidentops/actions",
    }
    # No state-changing GET: link prefetchers (Slack unfurl, mail scanners) would trigger it.
    assert all(w["httpMethod"] == "POST" for w in webhooks.values())


def test_slack_message_sends_people_to_the_web_ui_not_to_a_decision_link() -> None:
    body = json.dumps(_nodes()["Post Approval to Slack"]["parameters"])

    assert "/#/incidents/" in body
    assert "decision?" not in body
    assert "idempotency_key=" not in body


def test_actions_flow_checks_the_token_before_the_idempotency_gate() -> None:
    workflow = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    connections = workflow["connections"]

    assert connections["Approved Action Dispatch"]["main"][0][0]["node"] == "Verify Action Token"
    assert connections["Verify Action Token"]["main"][0][0]["node"] == "Action Idempotency Gate"


def test_actions_and_issue_creation_are_idempotent_and_use_approved_actions() -> None:
    nodes = _nodes()

    assert "$getWorkflowStaticData" in nodes["Action Idempotency Gate"]["parameters"]["jsCode"]
    assert "action:" in nodes["Action Idempotency Gate"]["parameters"]["jsCode"]
    issue = json.dumps(nodes["Create GitHub Incident"]["parameters"])
    assert "Idempotency-Key" in issue
    assert "approved_actions" in issue
    assert "X-API-Key" in json.dumps(nodes["Run LangGraph"])


def test_an_action_is_only_marked_done_after_the_issue_is_created() -> None:
    """A failed GitHub call must stay retryable, or retry-dispatch would be silently skipped."""
    nodes = _nodes()
    connections = _workflow()["connections"]

    gate = nodes["Action Idempotency Gate"]["parameters"]["jsCode"]
    assert "done:false" in gate and "seen[key].done" in gate
    assert connections["Create GitHub Incident"]["main"][0][0]["node"] == "Mark Action Done"
    assert connections["Mark Action Done"]["main"][0][0]["node"] == "Return Decision"
    assert "done:true" in nodes["Mark Action Done"]["parameters"]["jsCode"]


def test_every_connection_points_at_a_node_that_exists() -> None:
    workflow = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    names = {node["name"] for node in workflow["nodes"]}

    for source, branches in workflow["connections"].items():
        assert source in names
        for branch in branches["main"]:
            for edge in branch:
                assert edge["node"] in names


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
class TestVerifyActionTokenCode:
    """Run the workflow's real JavaScript in Node with n8n's globals stubbed."""

    @staticmethod
    def _run(headers: dict[str, str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        code = _nodes()["Verify Action Token"]["parameters"]["jsCode"]
        harness = textwrap.dedent(
            """
            const [code, headers, env] = JSON.parse(process.argv[1]);
            const item = { json: { headers, body: {} } };
            const $input = { first: () => item };
            const run = new Function('$json', '$env', '$input', code);
            try {
              const out = run(item.json, env, $input);
              console.log(out[0] === item ? 'PASS' : 'WRONG_ITEM');
            } catch (e) { console.log('THROW:' + e.message); }
            """
        )
        return subprocess.run(
            ["node", "-e", harness, json.dumps([code, headers, env])],
            capture_output=True,
            text=True,
            check=True,
        )

    def test_accepts_the_right_token(self) -> None:
        result = self._run({"authorization": "Bearer s3cret"}, {"N8N_ACTION_TOKEN": "s3cret"})
        assert result.stdout.strip() == "PASS"

    @pytest.mark.parametrize(
        "headers",
        [{"authorization": "Bearer wrong"}, {"authorization": "s3cret"}, {}],
    )
    def test_rejects_wrong_or_missing_tokens(self, headers: dict[str, str]) -> None:
        result = self._run(headers, {"N8N_ACTION_TOKEN": "s3cret"})
        assert result.stdout.strip() == "THROW:unauthorized"

    def test_fails_closed_when_no_secret_is_configured(self) -> None:
        result = self._run({"authorization": "Bearer "}, {})
        assert result.stdout.strip() == "THROW:N8N_ACTION_TOKEN is not configured"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_idempotency_gate_allows_a_retry_until_the_action_is_done() -> None:
    """Run the real gate and done-marker JavaScript against shared static data."""
    nodes = _nodes()
    harness = textwrap.dedent(
        """
        const [gateCode, doneCode, body] = JSON.parse(process.argv[1]);
        const store = {};
        const gate = () => new Function('$json', '$getWorkflowStaticData', gateCode)(
          { body }, () => store)[0].json;
        const done = () => new Function('$', '$getWorkflowStaticData', doneCode)(
          () => ({ first: () => ({ json: body }) }), () => store);
        const out = [];
        out.push(gate().execute);   // first delivery
        out.push(gate().execute);   // retry after a failed issue call
        done();
        out.push(gate().execute);   // duplicate after success
        console.log(JSON.stringify(out));
        """
    )
    result = subprocess.run(
        [
            "node",
            "-e",
            harness,
            json.dumps(
                [
                    nodes["Action Idempotency Gate"]["parameters"]["jsCode"],
                    nodes["Mark Action Done"]["parameters"]["jsCode"],
                    {"idempotency_key": "inc-1", "run_id": "inc-1"},
                ]
            ),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == [True, True, False]
