"""LangGraph assembly for the incident workflow."""

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from incidentops.core.config import Settings
from incidentops.workflows.incident.nodes import (
    assess,
    critic,
    finalize,
    intake,
    make_research,
    route_after_critic,
)
from incidentops.workflows.incident.state import IncidentState


def build_incident_graph(
    settings: Settings,
    checkpointer: BaseCheckpointSaver[Any],
) -> Any:
    graph = StateGraph(IncidentState)
    graph.add_node("intake", intake)
    # LangGraph's generic inference loses the TypedDict type for closure-built nodes.
    graph.add_node(
        "research",
        make_research(settings.runbook_db, settings.max_runbook_results),  # type: ignore[arg-type]
    )
    graph.add_node("assess", assess)
    graph.add_node("critic", critic)
    graph.add_node("finalize", finalize)
    graph.add_edge(START, "intake")
    graph.add_edge("intake", "research")
    graph.add_edge("research", "assess")
    graph.add_edge("assess", "critic")
    graph.add_conditional_edges(
        "critic",
        route_after_critic,
        {"research": "research", "finalize": "finalize"},
    )
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer)
