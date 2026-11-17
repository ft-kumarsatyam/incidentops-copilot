"""Typed state shared by every IncidentOps node."""

from typing import Any, Literal, TypedDict

RunStatus = Literal[
    "running",
    "awaiting_approval",
    "needs_human",
    "succeeded",
    "failed",
    "cancelled",
]


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
    conflicts: list[str]
    retries: int
    max_retries: int
    approval: Literal["approved", "rejected"] | None
    approval_required: bool
    status: RunStatus
    timeline: list[str]
