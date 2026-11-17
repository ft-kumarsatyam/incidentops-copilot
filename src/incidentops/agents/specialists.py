"""Experimental specialist subgraph used only by the architecture benchmark.

The specialists deliberately share the same deterministic evidence and safety contracts as the
baseline. This isolates topology: any benchmark difference comes from decomposition, not a new
model or relaxed guardrail.
"""

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from incidentops.agents.guardrails import deterministic_conflicts, measured_severity
from incidentops.agents.rules import RulesProvider
from incidentops.agents.schemas import (
    AIResult,
    CriticVerdict,
    ProposedAction,
    Triage,
    TriageContext,
)


class SpecialistState(TypedDict, total=False):
    context: TriageContext
    impact: dict[str, Any]
    change: dict[str, Any]
    runbook: dict[str, Any]
    triage: Triage


def _impact_specialist(state: SpecialistState) -> dict[str, Any]:
    context = state["context"]
    confidence = 0.8 - 0.15 * len(context.missing_fields)
    if context.evidence_refs - {"report"}:
        confidence += 0.2
    if context.monitoring.get("timed_out"):
        confidence -= 0.25
    return {
        "impact": {
            "severity": measured_severity(context),
            "confidence": max(0.0, min(1.0, confidence)),
            "conflicts": deterministic_conflicts(context),
        }
    }


def _change_specialist(state: SpecialistState) -> dict[str, Any]:
    context = state["context"]
    if not context.github.get("recent_deploy"):
        return {"change": {"hypotheses": [], "actions": []}}
    return {
        "change": {
            "hypotheses": ["A recent deploy may have introduced the regression"],
            "actions": [
                ProposedAction(
                    type="rollback",
                    description="Ask the service owner to assess the recent deploy for rollback",
                    evidence=["github"],
                )
            ],
        }
    }


def _runbook_specialist(state: SpecialistState) -> dict[str, Any]:
    hits = state["context"].runbook_hits
    if not hits:
        return {"runbook": {"actions": []}}
    hit = hits[0]
    return {
        "runbook": {
            "actions": [
                ProposedAction(
                    type="follow_runbook",
                    description=f"Follow runbook: {hit['title']} ({hit['path']})",
                    evidence=[f"runbook:{hit['path']}"],
                )
            ]
        }
    }


def _synthesize(state: SpecialistState) -> dict[str, Any]:
    context = state["context"]
    impact = state["impact"]
    service = context.service or "unknown service"
    actions = [
        ProposedAction(
            type="open_issue",
            description=f"Create {impact['severity']} incident record for {service}",
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
        *state["change"]["actions"],
        *state["runbook"]["actions"],
    ]
    return {
        "triage": Triage(
            severity=impact["severity"],
            confidence=impact["confidence"],
            hypotheses=state["change"]["hypotheses"],
            actions=actions,
            conflicts=impact["conflicts"],
            reasoning="Parallel impact, change, and runbook specialists; deterministic synthesis.",
        )
    }


def build_specialist_graph() -> Any:
    graph = StateGraph(SpecialistState)
    graph.add_node("impact_specialist", _impact_specialist)
    graph.add_node("change_specialist", _change_specialist)
    graph.add_node("runbook_specialist", _runbook_specialist)
    graph.add_node("synthesize", _synthesize)
    graph.add_edge(START, "impact_specialist")
    graph.add_edge(START, "change_specialist")
    graph.add_edge(START, "runbook_specialist")
    graph.add_edge("impact_specialist", "synthesize")
    graph.add_edge("change_specialist", "synthesize")
    graph.add_edge("runbook_specialist", "synthesize")
    graph.add_edge("synthesize", END)
    return graph.compile()


class SpecialistProvider:
    """Triage via a parallel specialist subgraph; review via the shared rules critic."""

    name = "specialist-rules"

    def __init__(self) -> None:
        self._graph = build_specialist_graph()
        self._critic = RulesProvider()

    def triage(self, context: TriageContext) -> AIResult[Triage]:
        result = self._graph.invoke({"context": context})
        return AIResult(value=result["triage"], provider=self.name)

    def review(self, context: TriageContext, triage: Triage) -> AIResult[CriticVerdict]:
        result = self._critic.review(context, triage)
        result.provider = self.name
        return result
