# ADR 001: LangGraph as the orchestrator

**Status:** Accepted
**Date:** 2026-11-17

## Context

The workflow must loop (retry research when evidence is missing), branch (confident,
conflicted, or incomplete), pause for a human, and survive a restart while it waits.
It should also be easy to explain on a whiteboard.

Alternatives considered: a linear Python script, a CrewAI role crew, n8n alone, Zapier.

## Decision

Use **LangGraph** for the reasoning workflow. Use **n8n** only for the integrations
that need credentials and retries (Slack, GitHub).

## Rationale

| Option | Pros | Cons |
|---|---|---|
| Linear script | Simple | No first-class cycles, weak pause and resume |
| CrewAI | Fast to set up roles | Control flow is implicit, harder to bound and test |
| n8n only | Great for webhooks and SaaS | Branching reasoning and typed state get messy |
| **LangGraph** | Explicit state machine, checkpointer, `interrupt()`, reducers | Learning curve |

## Consequences

- State is centralized in `IncidentState` (`workflows/incident/state.py`).
- Approval is an `interrupt()`; a decision resumes the thread with `Command(resume=...)`.
- The critic loop is bounded by `MAX_RESEARCH_ATTEMPTS`, so cost and latency have a ceiling.
- Persistence is a separate decision: [ADR 002](002-sqlite-checkpointer.md).

## References

- [Architecture](../ARCHITECTURE.md)
- [AI design](../AI_DESIGN.md)
