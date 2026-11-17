# RoleGraph orchestration

RoleGraph (`src/incidentops/orchestration/`) is a thin framework **on top of**
LangGraph. It does not replace the incident control graph; it only defines how
many cognitive roles run inside the `assess` step.

## When to use

| `AI_TOPOLOGY` | Behavior |
|---|---|
| `monolithic` | Single triage + shared critic (default) |
| `specialists` | Parallel impact/change/runbook roles → merge → critic |

## Modules

| Module | Role |
|---|---|
| `roles.py` | `RoleSpec` and handler protocol |
| `parallel.py` | Fan-out / join `StateGraph` |
| `merge.py` | Deterministic merge into `Triage` |
| `registry.py` | `TopologyRegistry`, `SpecialistCognitionProvider` |
| `factory.py` | `RoleGraphProvider` implementing `TriageProvider` |
| `provenance.py` | Per-role tokens on `state["ai"].roles` |

## Add a new role

1. Implement `RoleHandler` returning `AIResult[PartialOutcome]` in
   `rules_roles.py` or `llm_roles.py`.
2. Register the role in `register_default_topologies` or extend
   `_specialist_roles`.
3. Update merge rules if the role introduces new fields.
4. Add an eval case in `evals/dataset/` and run `make eval-architecture`.

## Cookbook: experiment with topology

```bash
AI_TOPOLOGY=specialists AI_PROVIDER=rules make demo
AI_TOPOLOGY=specialists AI_PROVIDER=claude make eval-architecture  # needs API key
```

Adoption gate: ≥5pp case-pass improvement, no safety regression — see
[ADR 006](adr/006-reject-specialist-swarm-for-current-scope.md) and
[ADR 008](adr/008-llm-specialists-and-mcp.md).

See [AI_SYSTEM_DESIGN.md](AI_SYSTEM_DESIGN.md) for the full flow catalog.