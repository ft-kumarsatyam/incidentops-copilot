# ADR 001: LangGraph as primary orchestrator

**Status:** Accepted  
**Date:** 2026-11-17

## Context

We need a system that:

- Decomposes goals into multi-step work
- Loops (re-fetch data, revise drafts)
- Branches on ambiguity and errors
- Supports **human-in-the-loop** interrupts
- Is demonstrable on a resume as "agentic," not "one prompt"

Alternatives considered: linear Python script, CrewAI role crews, n8n-only automation, Zapier.

## Decision

Use **LangGraph** as the core orchestrator for agent logic. Use **n8n** optionally for scheduling and lightweight integrations.

## Rationale

| Option | Pros | Cons |
|--------|------|------|
| Linear script | Simple | No first-class cycles, weak HITL story |
| CrewAI | Fast multi-role setup | Less explicit state machine for interviews |
| n8n-only | Great for cron/webhooks | LLM branching in nodes gets messy at scale |
| **LangGraph** | Explicit graph, checkpoints, interrupts | Learning curve |

LangGraph maps cleanly to whiteboard explanations: nodes, edges, conditional routing, state reducers.

## Consequences

- State schema is centralized (`AgentState` per workflow).
- CrewAI may be referenced in docs as comparison only unless we add a thin adapter later.
- n8n exports live under `n8n/` and call Python via webhook or CLI.

## References

- [Architecture](../ARCHITECTURE.md)
- [Walkthrough](../WALKTHROUGH.md)