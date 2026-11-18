# Load test: latency by replica count

Each virtual user reports an incident (`POST /incidents`) and then approves it (`POST /incidents/{id}/decision`), in a loop with no think time. Requests rotate across the replicas, and the approval always goes to a different replica than the creation, so every incident also crosses replicas through the shared Postgres. 30 s measured per cell after a 5 s warmup.

| Replicas | Users | Incidents/s | Incident p50 | Incident p95 | Incident p99 | Create p95 | Approve p95 | Postgres CPU | Errors |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 16 | 51.8 | 304 ms | 351 ms | 403 ms | 196 ms | 176 ms | 48% | 0 |
| 2 | 16 | 53.6 | 331 ms | 401 ms | 458 ms | 254 ms | 159 ms | 58% | 0 |
| 4 | 16 | 61.6 | 267 ms | 367 ms | 457 ms | 233 ms | 156 ms | 86% | 0 |
| 1 | 64 | 48.7 | 1331 ms | 1501 ms | 1578 ms | 867 ms | 746 ms | 46% | 0 |
| 2 | 64 | 55.3 | 1166 ms | 1468 ms | 1631 ms | 1212 ms | 399 ms | 67% | 0 |
| 4 | 64 | 62.8 | 1052 ms | 1387 ms | 1492 ms | 1263 ms | 487 ms | 90% | 0 |

## What it shows

- 16 users: 1 → 4 replicas moves throughput from 51.8 to 61.6 incidents/s (×1.19) and incident p95 from 351 to 367 ms.
- 64 users: 1 → 4 replicas moves throughput from 48.7 to 62.8 incidents/s (×1.29) and incident p95 from 1501 to 1387 ms.
- One incident costs Postgres about 37 committed transactions and 100 inserted rows, almost all of it LangGraph checkpoint writes, so the database is the shared resource that every replica queues on.
- Where busy Postgres sessions waited (4 replicas, 64 users, sampled mid-run): WAL flush (commit durability) 63%, audit chain head row lock 37%.
- Postgres peaked at 90% CPU (100% is one core) in a Docker VM with 2 cores.

An *incident* is one create plus one approval, timed together. Closed-loop users: with more users than the stack can serve, latency is mostly queueing, so compare rows with the same number of users. Repeated runs on one machine varied by roughly 10%, so differences smaller than that, such as ×1.19 against ×1.29, are noise.

## How this was run

- Machine: Apple M4, 10 logical cores, Darwin 25.5.0. Python 3.14.6, uvicorn 0.54.0.
- Replicas are `uvicorn` processes on that machine, one worker each, `EXECUTION_MODE=sync`, the offline rules provider (no model call), `RUNBOOK_STORE=postgres`.
- Postgres 17 (pgvector image, default durability settings) and Redis run in Docker (`compose.loadtest.yaml`, VM with 2 CPUs); the n8n webhook is a local stub that answers instantly.
- The load generator runs on the same machine and shares its cores with the replicas, so the numbers are for comparing replica counts, not absolute capacity.

Regenerate with `make loadtest` (needs Docker). Results vary with hardware; commit a new report only from a run you trust.
