# ADR 012: Target architecture and what we add next

**Status:** Accepted (Tier 1 largely built, see below)  
**Date:** 2026-11-21

## Context

The system now has identity, RBAC, tenants, a durable job queue, Postgres, Redis, a hash-chained
audit log and an optional decision-model advisor. A review against a broad engineering checklist
(API, auth, data, Redis, queues, reliability, AI, security, testing, DevOps, observability)
shows what is covered and what is not. The checklist is generic, so each item was judged on
value to this system and on the decisions already recorded here.

The full picture, diagrams and the coverage table are in
[END_TO_END_ARCHITECTURE.md](../END_TO_END_ARCHITECTURE.md).

## Decision

Adopt the three tiers in section 10 of that document as the roadmap, in this order:

1. **Tier 1:** inbound `Idempotency-Key`, circuit breakers around Claude, Jev and n8n, SSRF
   hardening, a role-aware UI, exported Jev and cost metrics, a `/v1` prefix. Built on
   2026-11-21 except the `/v1` prefix and a token-to-cost metric. Two deviations from the plan: the
   idempotency key is stored in the database (the run id is derived from tenant and key), not in
   Redis, so it needs no extra service and works across replicas; and approve and retry were
   already gated in the UI, so the UI work only closed the remaining gaps (Report link, samples,
   report page, role chip).
2. **Tier 2:** Redis Streams replay, cached role and key lookups with revocation, a dispatch
   outbox (built 2026-11-21, on the existing job queue), database-backed custom roles with ABAC
   rules (built 2026-11-21, [ADR 013](013-custom-roles-and-abac.md)), Postgres row-level security, Langfuse or an OTel collector, SLOs, Playwright and
   schemathesis.
3. **Tier 3:** per-user login (OIDC code with PKCE), a second LLM provider, tool calling and a
   real MCP client, reranking and richer evals, notifications and metering, Terraform and ECS.

Each item gets its own ADR or change when it is started. This ADR records the order and the
exclusions, not the designs.

**Not adopted**, and why:

- Celery, RabbitMQ, Kafka: the durable job queue already gives leases, retries and dead letters,
  and the load test shows the audit-chain row lock, not the queue, limits scaling.
- Next.js: [ADR 005](005-react-frontend.md). The UI is a static bundle served by the API.
- SQLAlchemy and Alembic: raw SQL with versioned, dual-dialect migrations is deliberate and works.
- WebSockets: [ADR 004](004-sse-over-websocket.md). Updates only flow server to client.
- GraphQL, gRPC, microservices, Kubernetes now: no requirement justifies the cost.

## Open decision

Scaling the audit chain needs a choice between per-tenant sharding of the chain and sealing it
asynchronously. Both change audit semantics, so this ADR does not pick one.

## Consequences

- Tier 1 is built; Tiers 2 and 3 are proposals. Nothing in them is built until its own change lands.
- Jev stays advisory ([ADR 011](011-jev-decision-advisor.md)) until it has been scored against the
  incident dataset.
- Doc claims about "today" must be re-checked when a tier item ships; the coverage table in the
  architecture document is the place to update.
