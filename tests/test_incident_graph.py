import json
from pathlib import Path

from workflow_automation.config import Settings
from workflow_automation.workflows.incident import run_incident

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_complete_incident_pauses_for_approval(tmp_path: Path) -> None:
    result = run_incident(
        load("incident_complete.json"),
        settings=Settings(checkpoint_dir=tmp_path),
    )

    assert result["status"] == "awaiting_approval"
    assert result["severity"] == "SEV-1"
    assert result["retries"] == 1
    assert result["missing_fields"] == []
    assert result["idempotency_key"] == "inc-demo-checkout"
    assert "__interrupt__" in result
    assert list(tmp_path.glob("*.json"))


def test_ambiguous_incident_retries_then_escalates(tmp_path: Path) -> None:
    result = run_incident(
        load("incident_ambiguous.json"),
        settings=Settings(checkpoint_dir=tmp_path, max_research_attempts=2),
    )

    assert result["status"] == "needs_human"
    assert result["retries"] == 2
    assert {"service", "owner", "symptoms"} <= set(result["missing_fields"])
    assert "__interrupt__" not in result


def test_conflicting_evidence_escalates(tmp_path: Path) -> None:
    result = run_incident(
        load("incident_conflict.json"),
        settings=Settings(checkpoint_dir=tmp_path),
    )

    assert result["status"] == "needs_human"
    assert result["conflicts"] == ["monitoring severity conflicts with measured impact"]


def test_preapproved_run_returns_action_payload_once(tmp_path: Path) -> None:
    payload = load("incident_duplicate_delivery.json")
    first = run_incident(
        payload,
        approval="approved",
        settings=Settings(checkpoint_dir=tmp_path),
    )
    second = run_incident(
        payload,
        approval="approved",
        settings=Settings(checkpoint_dir=tmp_path),
    )

    assert first["status"] == second["status"] == "succeeded"
    assert first["idempotency_key"] == second["idempotency_key"] == payload["run_id"]
    assert len(list(tmp_path.glob("*.json"))) == 1
