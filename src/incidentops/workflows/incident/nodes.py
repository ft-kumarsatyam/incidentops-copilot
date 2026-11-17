"""Incident graph nodes. Deterministic by design so graph behavior is testable."""

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from langgraph.types import interrupt

from incidentops.tools.monitoring import normalize_monitoring
from incidentops.tools.runbooks import search_runbooks
from incidentops.workflows.incident.state import IncidentState

REQUIRED_FIELDS = ("summary", "service", "reporter", "owner", "symptoms")


def stable_id(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str).encode()
    return f"inc-{hashlib.sha256(canonical).hexdigest()[:12]}"


def _missing(state: IncidentState) -> list[str]:
    return [field for field in REQUIRED_FIELDS if not state.get(field)]


def intake(state: IncidentState) -> dict[str, Any]:
    run_id = state.get("run_id") or stable_id(dict(state))
    return {
        "run_id": run_id,
        "idempotency_key": run_id,
        "status": "running",
        "retries": 0,
        "max_retries": state.get("max_retries", 2),
        "missing_fields": _missing(state),
        "evidence": [],
        "runbook_hits": [],
        "conflicts": [],
        "proposed_actions": [],
        "approval_required": True,
        "timeline": ["incident received"],
    }


def make_research(
    runbook_db: Path,
    max_runbook_results: int,
) -> Callable[[IncidentState], dict[str, Any]]:
    def research(state: IncidentState) -> dict[str, Any]:
        monitoring = normalize_monitoring(state.get("monitoring", {}))
        service = state.get("service") or str(monitoring.get("service", ""))
        query = " ".join([service, state.get("summary", ""), *state.get("symptoms", [])]).strip()
        runbook_hits = search_runbooks(runbook_db, query, max_runbook_results)
        evidence: list[dict[str, Any]] = []
        if monitoring:
            evidence.append({"source": "monitoring", "data": monitoring})
        if state.get("github"):
            evidence.append({"source": "github", "data": state["github"]})
        if runbook_hits:
            evidence.append({"source": "runbooks", "data": runbook_hits})
        update: dict[str, Any] = {
            "monitoring": monitoring,
            "evidence": evidence,
            "runbook_hits": runbook_hits,
            "retries": state.get("retries", 0) + 1,
            "timeline": [
                f"research attempt {state.get('retries', 0) + 1}",
                f"retrieved {len(runbook_hits)} runbook chunks",
            ],
        }
        if service:
            update["service"] = service
        return update

    return research


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
    if state.get("runbook_hits"):
        hit = state["runbook_hits"][0]
        actions.append(f"Follow runbook: {hit['title']} ({hit['path']})")
    return {
        "severity": severity,
        "confidence": confidence,
        "missing_fields": missing,
        "conflicts": conflicts,
        "proposed_actions": actions,
        "timeline": [f"assessed as {severity}"],
    }


def critic(state: IncidentState) -> dict[str, Any]:
    reasons: list[str] = []
    if state.get("missing_fields"):
        reasons.append("missing fields")
    if state.get("conflicts"):
        reasons.append("conflicting signals")
    threshold = state.get("confidence_threshold", 0.0)
    if state.get("confidence", 1.0) < threshold:
        reasons.append("confidence below threshold")
    exhausted = state.get("retries", 0) >= state.get("max_retries", 2)
    if reasons and exhausted:
        status = "needs_human"
    elif reasons:
        status = "running"
    else:
        status = "awaiting_approval"
    note = f"critic: {status}" + (f" ({', '.join(reasons)})" if reasons else "")
    return {"status": status, "timeline": [note]}


def route_after_critic(state: IncidentState) -> str:
    if state["status"] == "running":
        return "research"
    return "finalize"


def finalize(state: IncidentState) -> dict[str, Any]:
    """Pause for a human decision. Resumed via ``Command(resume=...)`` on the same thread."""
    if state["status"] == "needs_human":
        return {"approval_required": False, "timeline": ["escalated to a human"]}

    approval = state.get("approval")
    if approval is None:
        approval = interrupt(
            {
                "run_id": state["run_id"],
                "idempotency_key": state["idempotency_key"],
                "severity": state["severity"],
                "proposed_actions": state["proposed_actions"],
            }
        )
    return {
        "approval": approval,
        "approval_required": False,
        "status": "succeeded" if approval == "approved" else "cancelled",
        "timeline": [f"human decision: {approval}"],
    }
