# ADR 009: RoleGraph as cognitive layer on LangGraph

**Status:** Accepted  
**Date:** 2026-11-18

## Context

We need declarative multi-role cognition (parallel specialists) without adopting
CrewAI or a second orchestrator. LangGraph already owns checkpoints, loops, and
human `interrupt()`.

## Decision

Introduce **RoleGraph** in `src/incidentops/orchestration/` as a thin framework
that compiles parallel role subgraphs inside the `assess` step only. The outer
incident graph is unchanged.

## Consequences

- `TriageProvider` remains the workflow seam; `RoleGraphProvider` implements it.
- Topology is selected with `AI_TOPOLOGY=monolithic|specialists`.
- Specialists prototype in `agents/specialists.py` is backed by RoleGraph.
- CrewAI remains out of scope; fan-out/join is native LangGraph.

## References

- [AI system design](../AI_SYSTEM_DESIGN.md)
- [ORCHESTRATION.md](../ORCHESTRATION.md)
- [ADR 001](001-langgraph-as-orchestrator.md)