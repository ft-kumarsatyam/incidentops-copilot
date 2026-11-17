"""Incident graph nodes. Deterministic by design so graph behavior is testable."""

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from langgraph.types import interrupt

from workflow_automation.tools.monitoring import normalize_monitoring
from workflow_automation.workflows.incident.state import IncidentState

REQUIRED_FIELDS = ("summary", "service", "reporter", "owner", "symptoms")


def _stable_id(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str).encode()
    return f"inc-{hashlib.sha256(canonical).hexdigest()[:12]}"


def _missing(state: IncidentState) -> list[str]:
    return [field for field in REQUIRED_FIELDS if not state.get(field)]


def intake(state: IncidentState) -> dict[str, Any]:
    run_id = state.get("run_id") or _stable_id(dict(state))
    return {
        "run_id": run_id,
        "idempotency_key": run_id,
        "status": "running",
        "retries": 0,
        "max_retries": state.get("max_retries", 2),
        "missing_fields": _missing(state),
        "evidence": [],
        "conflicts": [],
        "proposed_actions": [],
        "approval_required": True,
        "timeline": ["incident received"],
    }


def research(state: IncidentState) -> dict[str, Any]:
    monitoring = normalize_monitoring(state.get("monitoring", {}))
    service = state.get("service") or str(monitoring.get("service", ""))
    evidence: list[dict[str, Any]] = []
    if monitoring:
        evidence.append({"source": "monitoring", "data": monitoring})
    if state.get("github"):
        evidence.append({"source": "github", "data": state["github"]})
    timeline = [*state.get("timeline", []), f"research attempt {state.get('retries', 0) + 1}"]
    update: dict[str, Any] = {
        "monitoring": monitoring,
        "evidence": evidence,
        "retries": state.get("retries", 0) + 1,
        "timeline": timeline,
    }
    if service:
        update["service"] = service
    return update


def assess(state: IncidentState) -> dict[str, Any]:
    monitoring = state.get("monitoring", {})
    error_rate = float(monitoring.get("error_rate", 0) or 0)
    symptoms = " ".join(state.get("symptoms", [])).lower()
    severity = "SEV-1" if error_rate >= 0.1 or "outage" in symptoms else "SEV-2"
    if error_rate < 0.03 and "outage" not in symptoms:
        severity = "SEV-3"

    conflicts: list[str] = []
    declared = str(monitoring.get("severity", "")).upper()
    if declared in {"SEV-3", "LOW"} and severity == "SEV-1":
        conflicts.append("monitoring severity conflicts with measured impact")

    missing = _missing(state)
    evidence_score = 0.2 if state.get("evidence") else 0.0
    confidence = max(0.0, min(1.0, 0.8 - 0.15 * len(missing) + evidence_score))
    actions = [
        f"Create {severity} incident record for {state.get('service') or 'unknown service'}",
        f"Assign incident commander: {state.get('owner') or 'unassigned'}",
        "Post investigation summary and next update time to the Slack thread",
    ]
    if state.get("github", {}).get("recent_deploy"):
        actions.append("Ask the service owner to assess the recent deploy for rollback")
    return {
        "severity": severity,
        "confidence": confidence,
        "missing_fields": missing,
        "conflicts": conflicts,
        "proposed_actions": actions,
        "timeline": [*state.get("timeline", []), f"assessed as {severity}"],
    }


def critic(state: IncidentState) -> dict[str, Any]:
    incomplete = bool(state.get("missing_fields") or state.get("conflicts"))
    exhausted = state.get("retries", 0) >= state.get("max_retries", 2)
    if incomplete and exhausted:
        status = "needs_human"
    elif incomplete:
        status = "running"
    else:
        status = "awaiting_approval"
    return {
        "status": status,
        "timeline": [*state.get("timeline", []), f"critic: {status}"],
    }


def route_after_critic(state: IncidentState) -> str:
    if state["status"] == "running":
        return "research"
    return "finalize"


def _persist(state: IncidentState, checkpoint_dir: Path) -> None:
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    safe_name = hashlib.sha256(state["run_id"].encode()).hexdigest()
    target = checkpoint_dir / f"{safe_name}.json"
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(target)


def make_finalize(checkpoint_dir: Path) -> Callable[[IncidentState], dict[str, Any]]:
    def finalize(state: IncidentState) -> dict[str, Any]:
        if state["status"] == "needs_human":
            _persist(state, checkpoint_dir)
            return {"approval_required": False}

        approval = state.get("approval")
        if approval is None:
            _persist(state, checkpoint_dir)
            approval = interrupt(
                {
                    "run_id": state["run_id"],
                    "idempotency_key": state["idempotency_key"],
                    "severity": state["severity"],
                    "proposed_actions": state["proposed_actions"],
                }
            )

        status = "succeeded" if approval == "approved" else "cancelled"
        update = {
            "approval": approval,
            "approval_required": False,
            "status": status,
            "timeline": [*state.get("timeline", []), f"human decision: {approval}"],
        }
        merged: dict[str, Any] = dict(state)
        merged.update(update)
        _persist(cast(IncidentState, merged), checkpoint_dir)
        return update

    return finalize
