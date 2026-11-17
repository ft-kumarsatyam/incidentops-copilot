"""Merge parallel specialist partials into a single Triage."""

from incidentops.agents.schemas import ProposedAction, Triage
from incidentops.orchestration.schemas import PartialOutcome


def merge_partials(
    partials: list[PartialOutcome],
    *,
    service: str,
    owner: str = "",
    default_severity: str = "SEV-2",
) -> Triage:
    impact = next((p for p in partials if p.role_id == "impact"), None)
    change = next((p for p in partials if p.role_id == "change"), None)
    runbook = next((p for p in partials if p.role_id == "runbook"), None)

    severity: str = impact.severity if impact and impact.severity else default_severity
    confidence = impact.confidence if impact and impact.confidence is not None else 0.5
    conflicts: list[str] = []
    for part in partials:
        conflicts.extend(part.conflicts)

    hypotheses: list[str] = []
    if change:
        hypotheses.extend(change.hypotheses)

    svc = service or "unknown service"
    actions: list[ProposedAction] = [
        ProposedAction(
            type="open_issue",
            description=f"Create {severity} incident record for {svc}",
            evidence=["report"],
        ),
        ProposedAction(
            type="assign_commander",
            description=f"Assign incident commander: {owner or 'unassigned'}",
            evidence=["report"],
        ),
        ProposedAction(
            type="notify",
            description="Post investigation summary and next update time to the Slack thread",
            evidence=["report"],
        ),
    ]
    for part in partials:
        actions.extend(part.actions)

    seen: set[tuple[str, str]] = set()
    deduped: list[ProposedAction] = []
    for action in actions:
        key = (action.type, action.description)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(action)

    reasoning_parts = [p.notes for p in partials if p.notes]
    if runbook and runbook.actions:
        reasoning_parts.append("runbook specialist cited retrieval hits")

    return Triage(
        severity=severity,  # type: ignore[arg-type]
        confidence=max(0.0, min(1.0, confidence)),
        hypotheses=hypotheses,
        actions=deduped,
        conflicts=list(dict.fromkeys(conflicts)),
        reasoning="; ".join(reasoning_parts)
        or "Parallel impact, change, and runbook specialists; RoleGraph merge.",
    )
