"""Deterministic checks that every model answer must pass before the workflow uses it."""

from incidentops.agents.schemas import (
    GuardrailError,
    ProposedAction,
    Severity,
    Triage,
    TriageContext,
)

MAX_ACTIONS = 8
MAX_HYPOTHESES = 5
MAX_TEXT = 300


def measured_severity(context: TriageContext) -> Severity:
    """Severity implied by the numbers alone. The model may differ, but must not contradict."""
    error_rate = float(context.monitoring.get("error_rate", 0) or 0)
    symptoms = " ".join(context.symptoms).lower()
    if error_rate >= 0.1 or "outage" in symptoms:
        return "SEV-1"
    if error_rate < 0.03:
        return "SEV-3"
    return "SEV-2"


def deterministic_conflicts(context: TriageContext) -> list[str]:
    declared = str(context.monitoring.get("severity", "")).upper()
    if declared in {"SEV-3", "LOW"} and measured_severity(context) == "SEV-1":
        return ["monitoring severity conflicts with measured impact"]
    return []


def _clip(text: str, limit: int = MAX_TEXT) -> str:
    return " ".join(text.split())[:limit]


def _is_grounded(action: ProposedAction, context: TriageContext) -> bool:
    if not action.evidence or not set(action.evidence) <= context.evidence_refs:
        return False
    if action.type == "rollback" and not context.github.get("recent_deploy"):
        return False  # never suggest a rollback without a deploy to roll back
    if action.type == "follow_runbook" and not any(
        ref.startswith("runbook:") for ref in action.evidence
    ):
        return False
    return True


def validate_triage(triage: Triage, context: TriageContext) -> Triage:
    """Return a safe, bounded triage or raise ``GuardrailError``."""
    seen: set[tuple[str, str]] = set()
    actions: list[ProposedAction] = []
    for action in triage.actions:
        description = _clip(action.description)
        key = (action.type, description.lower())
        if not description or key in seen or not _is_grounded(action, context):
            continue
        seen.add(key)
        actions.append(
            ProposedAction(type=action.type, description=description, evidence=action.evidence)
        )
    if not actions:
        raise GuardrailError("no proposed action was grounded in the available evidence")

    conflicts = [_clip(item) for item in triage.conflicts][:5]
    has_measurement = "error_rate" in context.monitoring and not context.monitoring.get("timed_out")
    measured = measured_severity(context)
    if has_measurement and {triage.severity, measured} == {"SEV-1", "SEV-3"}:
        conflicts.append(
            f"model severity ({triage.severity}) conflicts with measured impact ({measured})"
        )

    return Triage(
        severity=triage.severity,
        confidence=max(0.0, min(1.0, float(triage.confidence))),
        hypotheses=[_clip(item) for item in triage.hypotheses][:MAX_HYPOTHESES],
        actions=actions[:MAX_ACTIONS],
        conflicts=conflicts,
        reasoning=_clip(triage.reasoning, 1200),
    )
