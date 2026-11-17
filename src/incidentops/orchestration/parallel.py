"""Fan-out / join LangGraph subgraph for parallel roles."""

from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from incidentops.agents.schemas import TriageContext
from incidentops.orchestration.merge import merge_partials
from incidentops.orchestration.provenance import ProvenanceCollector
from incidentops.orchestration.roles import RoleHandler, RoleSpec
from incidentops.orchestration.schemas import PartialOutcome


def _merge_partials(
    left: dict[str, PartialOutcome], right: dict[str, PartialOutcome]
) -> dict[str, PartialOutcome]:
    return {**left, **right}


class ParallelState(TypedDict, total=False):
    context: TriageContext
    provenance: ProvenanceCollector
    partials: Annotated[dict[str, PartialOutcome], _merge_partials]
    triage: Any


def build_parallel_stage(
    roles: list[RoleSpec],
    *,
    topology_id: str,
    join_handler: RoleHandler | None = None,
) -> Any:
    """Compile a parallel role subgraph; join_handler may replace default merge."""

    if not roles:
        raise ValueError("ParallelStage requires at least one role")

    def make_role_node(spec: RoleSpec) -> Any:
        def role_node(state: ParallelState) -> dict[str, Any]:
            context = state["context"]
            collector = state["provenance"]
            result = spec.handler(context)
            collector.record(spec.id, result)
            partials = dict(state.get("partials", {}))
            partials[spec.id] = result.value
            return {"partials": partials}

        return role_node

    def merge_node(state: ParallelState) -> dict[str, Any]:
        context = state["context"]
        partials_dict = state.get("partials", {})
        ordered = [partials_dict[spec.id] for spec in roles if spec.id in partials_dict]
        if join_handler is not None:
            join_result = join_handler(context)
            state["provenance"].record("synthesize", join_result)
        triage = merge_partials(ordered, service=context.service, owner=context.owner)
        return {"triage": triage}

    graph = StateGraph(ParallelState)
    merge_id = "merge"
    for spec in roles:
        node_id = f"role_{spec.id}"
        graph.add_node(node_id, make_role_node(spec))
        graph.add_edge(START, node_id)
        graph.add_edge(node_id, merge_id)
    graph.add_node(merge_id, merge_node)
    graph.add_edge(merge_id, END)
    return graph.compile()


def invoke_parallel_stage(
    compiled: Any,
    context: TriageContext,
    *,
    topology_id: str,
) -> tuple[Any, ProvenanceCollector]:
    collector = ProvenanceCollector(topology=topology_id)
    result = compiled.invoke(
        {"context": context, "provenance": collector, "partials": {}}
    )
    return result["triage"], collector