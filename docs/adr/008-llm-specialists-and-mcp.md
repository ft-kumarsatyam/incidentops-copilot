# ADR 008: LLM specialists and MCP bridge (opt-in)

**Status:** Accepted  
**Date:** 2026-11-18

## Context

Portfolio and integration goals require parallel LLM roles, optional MCP read
context, and email/Slack side paths — without weakening human-in-the-loop
dispatch.

## Decision

1. **LLM specialists** — When `AI_TOPOLOGY=specialists` and a Claude key is set,
   impact/change/runbook roles call structured partial schemas; merge + shared
   guardrails produce `Triage`. Default remains `monolithic`.

2. **MCP** — `MCP_ENABLED` adds read-only evidence via `ToolBroker` during
   research. External MCP SDK is optional (`[mcp]` extra). Writes stay on n8n.

3. **Adoption** — Same gate as ADR 006: promote specialists to default only if
   `make eval-architecture` shows ≥5pp case-pass gain with no safety regression.

## Consequences

- Rules path unchanged for CI (`make eval`, `make eval-architecture`).
- `state["ai"]` may include `topology` and `roles[]` for UI provenance.
- INTEGRATIONS.md documents Slack, email, MCP setup.

## References

- [ADR 006](006-reject-specialist-swarm-for-current-scope.md)
- [INTEGRATIONS.md](../INTEGRATIONS.md)