# ADR 002: SQLite for checkpoints, incidents and audit

**Status:** Accepted
**Date:** 2026-11-17

## Context

Runs must survive a restart while they wait for approval, and the UI needs to list and
search them. The first version rebuilt an in-memory graph on every call and kept
hand-written JSON checkpoints, so a decision re-ran the graph from the start and lost
its history.

## Decision

Compile the graph once with LangGraph's `SqliteSaver`, keyed by `thread_id = run_id`.
Keep a small `incidents` read model and an append-only `audit_log` in the same database
file. The status transitions use an atomic `claim(run_id, expected)` through a transient
`busy` status, so concurrent decisions cannot both proceed.

## Alternatives

- **Postgres:** the right answer for several API instances. It needs a service to run,
  which works against "clone it and try it in a minute".
- **JSON files:** what we had. No transactions, no querying, easy to corrupt.
- **In-memory only:** loses every pending approval on restart.

## Consequences

- One file to back up, and zero setup.
- One writer process. WAL mode allows concurrent readers, but run a single API instance.
- The checkpoint is the source of truth. The read model can be rebuilt from it.
- Migration path: swap in `PostgresSaver`, port `IncidentRepo` (two tables), and replace
  the in-process event bus. See [DEPLOYMENT](../DEPLOYMENT.md).
