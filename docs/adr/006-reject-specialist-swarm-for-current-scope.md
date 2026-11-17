# ADR 006: Reject a specialist swarm for the current scope

**Status:** Accepted  
**Date:** 2026-11-17

## Context

The current workflow uses one triage provider and one critic. We wanted evidence
before adding CrewAI or decomposing triage into more agents.

## Experiment

`agents/specialists.py` implements a real nested LangGraph:

```text
                 impact specialist ─┐
incident context → change specialist ├→ deterministic synthesis → shared critic
                 runbook specialist ┘
```

Both architectures receive the same ten labeled incidents, evidence, runbooks,
action schema, guardrails, and scorer. The specialist graph changes topology
only. The adoption gate requires at least five percentage points of case-pass
improvement with no safety or grounding regression.

## Result

| Architecture | Roles | Cases passed | Safety | Grounded | Median |
|---|---:|---:|---:|---:|---:|
| Current graph | 2 | 100% | 100% | 100% | 4.83 ms |
| Parallel specialists | 5 | 100% | 100% | 100% | 8.64 ms |

Exact timings are machine-dependent; regenerate
[`evals/architecture_report.md`](../../evals/architecture_report.md) with
`make eval-architecture`.

## Decision

Keep the current architecture. The specialist graph adds three roles and a
fan-out/join without improving any labeled outcome. CrewAI would add another
orchestration dependency while LangGraph already expresses the experiment.

Reconsider only after new labeled failures cluster by independent domains—for
example, change analysis and customer-impact analysis require different tools
or context windows—and the same benchmark clears the adoption gate.

