"""Incident graph nodes.

Agents (triage, critic) are injected as a provider so the same graph runs with Claude, with the
deterministic rules provider, or with a fake in tests. Anything that gates the workflow
(missing fields, confidence threshold, bounded retries, human approval) stays in plain code.
"""

import hashlib
import json
from collections.abc import Callable
from typing import Any

import httpx
from langgraph.types import interrupt

from incidentops.agents.guardrails import deterministic_conflicts
from incidentops.agents.resilient import TriageProvider
from incidentops.agents.schemas import AIResult, ProposedAction, Triage, TriageContext
from incidentops.core.config import Settings
from incidentops.core.logging import get_logger
from incidentops.tools.github import fetch_recent_commit
from incidentops.tools.monitoring import normalize_monitoring
from incidentops.tools.runbooks import search_runbooks
from incidentops.workflows.incident.state import IncidentState

log = get_logger(__name__)
REQUIRED_FIELDS = ("summary", "service", "reporter", "owner", "symptoms")

Node = Callable[[IncidentState], dict[str, Any]]


def stable_id(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str).encode()
    return f"inc-{hashlib.sha256(canonical).hexdigest()[:12]}"


def _missing(state: IncidentState) -> list[str]:
    return [field for field in REQUIRED_FIELDS if not state.get(field)]


def context_from_state(state: IncidentState) -> TriageContext:
    return TriageContext(
        summary=state.get("summary", ""),
        service=state.get("service", ""),
        owner=state.get("owner", ""),
        reporter=state.get("reporter", ""),
        symptoms=list(state.get("symptoms", [])),
        monitoring=dict(state.get("monitoring", {})),
        github=dict(state.get("github", {})),
        runbook_hits=list(state.get("runbook_hits", [])),
        missing_fields=_missing(state),
    )


def _ai_summary(result: AIResult[Any]) -> dict[str, Any]:
    return {
        "provider": result.provider,
        "fallback_reason": result.fallback_reason,
        "input_tokens": result.usage.input_tokens,
        "output_tokens": result.usage.output_tokens,
    }


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


def make_research(settings: Settings) -> Node:
    """Researcher: gathers monitoring, GitHub and runbook evidence.

    Each retry widens the runbook search and re-attempts a GitHub fetch that failed earlier,
    so a second pass can actually find something the first one missed.
    """

    def research(state: IncidentState) -> dict[str, Any]:
        attempt = state.get("retries", 0) + 1
        timeline = [f"research attempt {attempt}"]
        monitoring = normalize_monitoring(state.get("monitoring", {}))
        service = state.get("service") or str(monitoring.get("service", ""))

        github = dict(state.get("github", {}))
        if settings.github_token and github.get("repo") and not github.get("recent_deploy"):
            try:
                github = {**github, **fetch_recent_commit(github["repo"], settings.github_token)}
                timeline.append("fetched latest commit from GitHub")
            except httpx.HTTPError as error:
                log.warning("research.github_failed", error=type(error).__name__)
                timeline.append(f"github unavailable ({type(error).__name__})")

        query = " ".join([service, state.get("summary", ""), *state.get("symptoms", [])]).strip()
        runbook_hits = search_runbooks(
            settings.runbook_db, query, settings.max_runbook_results * attempt, prefer=service
        )
        timeline.append(f"retrieved {len(runbook_hits)} runbook chunks")
        if monitoring.get("timed_out"):
            timeline.append("monitoring timed out; impact is unverified")

        evidence: list[dict[str, Any]] = []
        if monitoring:
            evidence.append({"source": "monitoring", "data": monitoring})
        if github:
            evidence.append({"source": "github", "data": github})
        if runbook_hits:
            evidence.append({"source": "runbooks", "data": runbook_hits})
        update: dict[str, Any] = {
            "monitoring": monitoring,
            "github": github,
            "evidence": evidence,
            "runbook_hits": runbook_hits,
            "retries": attempt,
            "timeline": timeline,
        }
        if service:
            update["service"] = service
        return update

    return research


def make_assess(provider: TriageProvider) -> Node:
    """Triage agent: severity, hypotheses and grounded actions (validated by guardrails)."""

    def assess(state: IncidentState) -> dict[str, Any]:
        context = context_from_state(state)
        result = provider.triage(context)
        triage = result.value
        conflicts = list(dict.fromkeys([*triage.conflicts, *deterministic_conflicts(context)]))
        # Missing information caps confidence no matter how sure the model sounds.
        confidence = min(triage.confidence, 1.0 - 0.15 * len(context.missing_fields))
        return {
            "severity": triage.severity,
            "confidence": max(0.0, confidence),
            "missing_fields": context.missing_fields,
            "conflicts": conflicts,
            "hypotheses": triage.hypotheses,
            "proposed_actions": [action.description for action in triage.actions],
            "action_items": [action.model_dump() for action in triage.actions],
            "ai": _ai_summary(result),
            "timeline": [f"assessed as {triage.severity} by {result.provider}"],
        }

    return assess


def _triage_from_state(state: IncidentState) -> Triage:
    return Triage(
        severity=state.get("severity", "SEV-2"),  # type: ignore[arg-type]
        confidence=state.get("confidence", 0.0),
        hypotheses=state.get("hypotheses", []),
        actions=[ProposedAction(**item) for item in state.get("action_items", [])],
        conflicts=state.get("conflicts", []),
        reasoning="",
    )


def make_critic(provider: TriageProvider) -> Node:
    """Critic: hard gates run in code; the model reviewer can add concerns but never waive one."""

    def critic(state: IncidentState) -> dict[str, Any]:
        reasons: list[str] = []
        if state.get("missing_fields"):
            reasons.append("missing fields")
        if state.get("conflicts"):
            reasons.append("conflicting signals")
        if state.get("confidence", 1.0) < state.get("confidence_threshold", 0.0):
            reasons.append("confidence below threshold")

        update: dict[str, Any] = {}
        escalate = False
        if not reasons:  # no point spending a model call on a proposal that will loop anyway
            review = provider.review(context_from_state(state), _triage_from_state(state))
            update["ai_review"] = {**_ai_summary(review), "verdict": review.value.verdict}
            if review.value.verdict != "approve":
                reasons.extend(review.value.issues or [f"critic verdict: {review.value.verdict}"])
                escalate = review.value.verdict == "escalate"

        exhausted = state.get("retries", 0) >= state.get("max_retries", 2)
        if reasons and (exhausted or escalate):
            status = "needs_human"
        elif reasons:
            status = "running"
        else:
            status = "awaiting_approval"
        note = f"critic: {status}" + (f" ({', '.join(reasons)})" if reasons else "")
        return {**update, "status": status, "timeline": [note]}

    return critic


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
