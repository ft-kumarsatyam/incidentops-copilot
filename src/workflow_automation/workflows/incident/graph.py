"""LangGraph assembly and public IncidentOps runner."""

import hashlib
import json
from typing import Any

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from workflow_automation.config import Settings
from workflow_automation.workflows.incident.nodes import (
    assess,
    critic,
    intake,
    make_finalize,
    research,
    route_after_critic,
)
from workflow_automation.workflows.incident.state import IncidentState


def build_incident_graph(settings: Settings | None = None) -> Any:
    settings = settings or Settings()
    graph = StateGraph(IncidentState)
    graph.add_node("intake", intake)
    graph.add_node("research", research)
    graph.add_node("assess", assess)
    graph.add_node("critic", critic)
    # LangGraph's generic inference loses the TypedDict type for closure-built nodes.
    graph.add_node("finalize", make_finalize(settings.checkpoint_dir))  # type: ignore[arg-type]
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
    return graph.compile(checkpointer=MemorySaver())


def run_incident(
    payload: dict[str, Any],
    *,
    approval: str | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or Settings()
    initial = dict(payload)
    if not initial.get("run_id"):
        canonical = json.dumps(payload, sort_keys=True, default=str).encode()
        initial["run_id"] = f"inc-{hashlib.sha256(canonical).hexdigest()[:12]}"
    initial["max_retries"] = settings.max_research_attempts
    if approval:
        initial["approval"] = approval
    graph = build_incident_graph(settings)
    result = graph.invoke(
        initial,
        config={"configurable": {"thread_id": initial["run_id"]}},
    )
    if not isinstance(result, dict):
        raise TypeError("incident graph must return a state mapping")
    return result
