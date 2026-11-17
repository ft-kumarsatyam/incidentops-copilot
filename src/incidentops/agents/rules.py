"""Deterministic provider: the offline default and the safety net for model failures."""

from incidentops.agents.guardrails import deterministic_conflicts, measured_severity
from incidentops.agents.schemas import (
    AIResult,
    CriticVerdict,
    ProposedAction,
    Triage,
    TriageContext,
)


class RulesProvider:
    name = "rules"

    def triage(self, context: TriageContext) -> AIResult[Triage]:
        severity = measured_severity(context)
        service = context.service or "unknown service"
        actions = [
            ProposedAction(
                type="open_issue",
                description=f"Create {severity} incident record for {service}",
                evidence=["report"],
            ),
            ProposedAction(
                type="assign_commander",
                description=f"Assign incident commander: {context.owner or 'unassigned'}",
                evidence=["report"],
            ),
            ProposedAction(
                type="notify",
                description="Post investigation summary and next update time to the Slack thread",
                evidence=["report"],
            ),
        ]
        hypotheses: list[str] = []
        if context.github.get("recent_deploy"):
            hypotheses.append("A recent deploy may have introduced the regression")
            actions.append(
                ProposedAction(
                    type="rollback",
                    description="Ask the service owner to assess the recent deploy for rollback",
                    evidence=["github"],
                )
            )
        if context.runbook_hits:
            hit = context.runbook_hits[0]
            actions.append(
                ProposedAction(
                    type="follow_runbook",
                    description=f"Follow runbook: {hit['title']} ({hit['path']})",
                    evidence=[f"runbook:{hit['path']}"],
                )
            )

        confidence = 0.8 - 0.15 * len(context.missing_fields)
        if context.evidence_refs - {"report"}:
            confidence += 0.2
        if context.monitoring.get("timed_out"):
            confidence -= 0.25  # impact is unverified, so ask a human rather than guess
        return AIResult(
            value=Triage(
                severity=severity,
                confidence=max(0.0, min(1.0, confidence)),
                hypotheses=hypotheses,
                actions=actions,
                conflicts=deterministic_conflicts(context),
                reasoning="Severity derived from measured error rate and reported symptoms.",
            ),
            provider=self.name,
        )

    def review(self, context: TriageContext, triage: Triage) -> AIResult[CriticVerdict]:
        issues = [*triage.conflicts, *(f"missing {name}" for name in context.missing_fields)]
        return AIResult(
            value=CriticVerdict(
                verdict="revise" if issues else "approve",
                issues=issues,
                notes="Rule-based review.",
            ),
            provider=self.name,
        )
