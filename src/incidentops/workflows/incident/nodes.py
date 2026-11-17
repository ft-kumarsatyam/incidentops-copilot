"""Incident graph nodes.

Agents (triage, critic) are injected as a provider so the same graph runs with Claude, with the
deterministic rules provider, or with a fake in tests. Anything that gates the workflow
(missing fields, confidence threshold, bounded retries, human approval) stays in plain code.
"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import httpx
from langgraph.types import interrupt

from incidentops.agents.guardrails import deterministic_conflicts, injection_signals
from incidentops.agents.resilient import TriageProvider
from incidentops.agents.schemas import AIResult, ProposedAction, Triage, TriageContext
from incidentops.core.config import Settings
from incidentops.core.logging import get_logger
from incidentops.tools.github import fetch_recent_commit, is_valid_repo, normalize_github
from incidentops.tools.monitoring import normalize_monitoring
from incidentops.tools.query_rewrite import expand_runbook_query, rewrite_metadata
from incidentops.tools.retrieval import RunbookRetriever, build_runbook_retriever
from incidentops.workflows.incident.state import IncidentState

log = get_logger(__name__)
REQUIRED_FIELDS = ("summary", "service", "reporter", "owner", "symptoms")
# Defence in depth: the API already caps these, but the CLI and tests call the service directly.
MAX_SUMMARY = 2000
MAX_SYMPTOMS = 20
MAX_SYMPTOM_CHARS = 500

Node = Callable[[IncidentState], dict[str, Any]]


def stable_id(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str).encode()
    return f"inc-{hashlib.sha256(canonical).hexdigest()[:12]}"


def _missing(state: IncidentState) -> list[str]:
    return [field for field in REQUIRED_FIELDS if not state.get(field)]


def context_from_state(state: IncidentState) -> TriageContext:
    context = TriageContext(
        summary=str(state.get("summary", ""))[:MAX_SUMMARY],
        service=str(state.get("service", ""))[:128],
        owner=str(state.get("owner", ""))[:128],
        reporter=str(state.get("reporter", ""))[:128],
        symptoms=[
            str(s)[:MAX_SYMPTOM_CHARS] for s in list(state.get("symptoms", []))[:MAX_SYMPTOMS]
        ],
        monitoring=normalize_monitoring(state.get("monitoring", {})),
        github=normalize_github(state.get("github", {})),
        runbook_hits=list(state.get("runbook_hits", [])),
        missing_fields=_missing(state),
    )
    return replace(context, guardrail_flags=injection_signals(context))


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


def make_research(settings: Settings, retriever: RunbookRetriever | None = None) -> Node:
    """Researcher: gathers monitoring, GitHub and runbook evidence.

    Each retry widens the runbook search and re-attempts a GitHub fetch that failed earlier,
    so a second pass can actually find something the first one missed.
    """

    def research(state: IncidentState) -> dict[str, Any]:
        attempt = state.get("retries", 0) + 1
        timeline = [f"research attempt {attempt}"]
        monitoring = normalize_monitoring(state.get("monitoring", {}))
        service = state.get("service") or str(monitoring.get("service", ""))

        github = normalize_github(state.get("github", {}))
        if state.get("github") and not github.get("repo") and "repo" in state["github"]:
            timeline.append("github repo ignored: not a valid owner/name")
        if (
            settings.github_token
            and is_valid_repo(github.get("repo"))
            and not github.get("recent_deploy")
        ):
            try:
                github = {**github, **fetch_recent_commit(github["repo"], settings.github_token)}
                timeline.append("fetched latest commit from GitHub")
            except (httpx.HTTPError, ValueError) as error:
                log.warning("research.github_failed", error=type(error).__name__)
                timeline.append(f"github unavailable ({type(error).__name__})")

        raw_query = " ".join(
            [service, state.get("summary", ""), *state.get("symptoms", [])]
        ).strip()
        search_query = expand_runbook_query(
            raw_query,
            service=service,
            symptoms=[str(s) for s in state.get("symptoms", [])],
        )
        rb = retriever or build_runbook_retriever(settings)
        runbook_hits = rb.search(
            search_query,
            settings.max_runbook_results * attempt,
            prefer=service or None,
        )
        timeline.append(f"retrieved {len(runbook_hits)} runbook chunks")
        if search_query != raw_query:
            timeline.append("expanded runbook query with service aliases")
        if monitoring.get("unverified_reason"):
            timeline.append(
                f"monitoring unusable ({monitoring['unverified_reason']}); impact is unverified"
            )
        elif monitoring.get("timed_out"):
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
            **rewrite_metadata(raw_query, search_query, service),
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
            "guardrail_flags": context.guardrail_flags,
            "timeline": [
                f"assessed as {triage.severity} by {result.provider}",
                *(
                    [f"possible prompt injection in: {', '.join(context.guardrail_flags)}"]
                    if context.guardrail_flags
                    else []
                ),
            ],
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
    approved_actions = state.get("approved_actions")
    if approval is None:
        resumed = interrupt(
            {
                "run_id": state["run_id"],
                "idempotency_key": state["idempotency_key"],
                "severity": state["severity"],
                "proposed_actions": state["proposed_actions"],
            }
        )
        # A bare string is still accepted so older callers and tests keep working.
        if isinstance(resumed, dict):
            approval = resumed["decision"]
            approved_actions = resumed.get("approved_actions")
        else:
            approval = resumed
    if approved_actions is None:
        approved_actions = state["proposed_actions"] if approval == "approved" else []
    return {
        "approved_actions": approved_actions,
        "approval": approval,
        "approval_required": False,
        "status": "succeeded" if approval == "approved" else "cancelled",
        "timeline": [f"human decision: {approval}"],
    }
