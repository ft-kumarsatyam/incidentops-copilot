# Agent architecture evaluation

Same labeled incidents, runbooks, safety checks, and deterministic logic; only the orchestration topology changes.

| Architecture | Roles | Cases passed | Safety | Grounded | Median ms | p95 ms |
|---|---:|---:|---:|---:|---:|---:|
| Current graph | 2 | 100% | 100% | 100% | 4.83 | 8.97 |
| Parallel specialists | 5 | 100% | 100% | 100% | 8.64 | 32.23 |

## Decision

**REJECT specialist swarm for the current scope.** No quality gain; the additional roles and nested graph are unjustified.

Adoption gate: at least 5 percentage points of case-pass improvement, no safety or grounding regression. Latency is reported, not used to excuse lower quality.

CrewAI remains unnecessary: LangGraph already expresses the tested fan-out and join.
