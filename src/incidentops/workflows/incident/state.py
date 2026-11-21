"""Typed state shared by every IncidentOps node."""

import operator
from typing import Annotated, Any, Literal, TypedDict

RunStatus = Literal[
    "queued",
    "running",
    "awaiting_approval",
    "needs_human",
    "succeeded",
    "failed",
    "cancelled",
    "errored",
]

ActionDispatch = Literal["sent", "not_configured", "failed"]


class IncidentState(TypedDict, total=False):
    run_id: str
    idempotency_key: str
    channel_id: str
    thread_ts: str
    reporter: str
    summary: str
    service: str
    owner: str
    symptoms: list[str]
    monitoring: dict[str, Any]
    github: dict[str, Any]
    evidence: list[dict[str, Any]]
    runbook_hits: list[dict[str, Any]]
    missing_fields: list[str]
    severity: str
    confidence: float
    proposed_actions: list[str]
    action_items: list[dict[str, Any]]
    approved_actions: list[str]
    hypotheses: list[str]
    ai: dict[str, Any]
    ai_review: dict[str, Any]
    jev: dict[str, Any]
    conflicts: list[str]
    guardrail_flags: list[str]
    retries: int
    max_retries: int
    confidence_threshold: float
    approval: Literal["approved", "rejected"] | None
    approval_required: bool
    action_dispatch: ActionDispatch
    status: RunStatus
    # Append-only: nodes return only the new entries and the reducer concatenates them.
    timeline: Annotated[list[str], operator.add]
