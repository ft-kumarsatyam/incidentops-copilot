"""Deterministic specialist role handlers (benchmark and offline mode)."""

from incidentops.agents.guardrails import deterministic_conflicts, measured_severity
from incidentops.agents.schemas import AIResult, ProposedAction, TriageContext, Usage
from incidentops.orchestration.schemas import PartialOutcome


def impact_role(context: TriageContext) -> AIResult[PartialOutcome]:
    confidence = 0.8 - 0.15 * len(context.missing_fields)
    if context.evidence_refs - {"report"}:
        confidence += 0.2
    if context.monitoring.get("timed_out"):
        confidence -= 0.25
    return AIResult(
        value=PartialOutcome(
            role_id="impact",
            severity=measured_severity(context),
            confidence=max(0.0, min(1.0, confidence)),
            conflicts=deterministic_conflicts(context),
            notes="impact specialist",
        ),
        provider="rules:impact",
        usage=Usage(),
    )


def change_role(context: TriageContext) -> AIResult[PartialOutcome]:
    if not context.github.get("recent_deploy"):
        return AIResult(
            value=PartialOutcome(role_id="change", notes="no recent deploy signal"),
            provider="rules:change",
            usage=Usage(),
        )
    return AIResult(
        value=PartialOutcome(
            role_id="change",
            hypotheses=["A recent deploy may have introduced the regression"],
            actions=[
                ProposedAction(
                    type="rollback",
                    description="Ask the service owner to assess the recent deploy for rollback",
                    evidence=["github"],
                )
            ],
            notes="change specialist",
        ),
        provider="rules:change",
        usage=Usage(),
    )


def runbook_role(context: TriageContext) -> AIResult[PartialOutcome]:
    hits = context.runbook_hits
    if not hits:
        return AIResult(
            value=PartialOutcome(role_id="runbook", notes="no runbook hit"),
            provider="rules:runbook",
            usage=Usage(),
        )
    hit = hits[0]
    return AIResult(
        value=PartialOutcome(
            role_id="runbook",
            actions=[
                ProposedAction(
                    type="follow_runbook",
                    description=f"Follow runbook: {hit['title']} ({hit['path']})",
                    evidence=[f"runbook:{hit['path']}"],
                )
            ],
            notes=f"runbook specialist: {hit['path']}",
        ),
        provider="rules:runbook",
        usage=Usage(),
    )
