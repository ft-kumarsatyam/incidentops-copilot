# Load test: latency by replica count

Each virtual user reports an incident (`POST /incidents`) and then approves it (`POST /incidents/{id}/decision`), in a loop with no think time. Requests rotate across the replicas, and the approval always goes to a different replica than the creation, so every incident also crosses replicas through the shared Postgres. 30 s measured per cell after a 5 s warmup.

| Checkpoints | Replicas | Users | Incidents/s | Incident p50 | Incident p95 | Incident p99 | Create p95 | Approve p95 | Postgres CPU | Errors |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| async | 1 | 16 | 36.7 | 381 ms | 824 ms | 1064 ms | 451 ms | 376 ms | 55% | 0 |
| async | 2 | 16 | 40.0 | 417 ms | 615 ms | 813 ms | 384 ms | 245 ms | 60% | 0 |
| async | 4 | 16 | 44.4 | 362 ms | 533 ms | 617 ms | 337 ms | 236 ms | 110% | 0 |
| exit | 1 | 16 | 59.4 | 265 ms | 309 ms | 372 ms | 118 ms | 205 ms | 54% | 0 |
| exit | 2 | 16 | 73.5 | 213 ms | 282 ms | 389 ms | 108 ms | 191 ms | 78% | 0 |
| exit | 4 | 16 | 77.5 | 196 ms | 283 ms | 433 ms | 122 ms | 188 ms | 95% | 0 |
| async | 1 | 64 | 40.5 | 1573 ms | 2099 ms | 2252 ms | 1136 ms | 1010 ms | 45% | 0 |
| async | 2 | 64 | 34.8 | 1703 ms | 2993 ms | 3594 ms | 2602 ms | 807 ms | 65% | 0 |
| async | 4 | 64 | 40.1 | 1516 ms | 2590 ms | 2804 ms | 2275 ms | 1260 ms | 93% | 0 |
| exit | 1 | 64 | 62.8 | 1031 ms | 1143 ms | 1264 ms | 499 ms | 717 ms | 54% | 0 |
| exit | 2 | 64 | 68.3 | 846 ms | 1756 ms | 2634 ms | 905 ms | 995 ms | 82% | 0 |
| exit | 4 | 64 | 68.5 | 927 ms | 1492 ms | 1757 ms | 561 ms | 1228 ms | 94% | 0 |

## What it shows

- `CHECKPOINT_DURABILITY=async`: 16 users, 1 → 4 replicas: throughput 36.7 → 44.4 incidents/s (×1.21), incident p95 824 → 533 ms.
- `CHECKPOINT_DURABILITY=async`: 64 users, 1 → 4 replicas: throughput 40.5 → 40.1 incidents/s (×0.99), incident p95 2099 → 2590 ms.
- `CHECKPOINT_DURABILITY=async`: one incident costs Postgres about 37 committed transactions and 100 inserted rows.
- `CHECKPOINT_DURABILITY=async`: where busy Postgres sessions waited (4 replicas, 64 users, sampled mid-run): audit chain head row lock 83%, WAL flush (commit durability) 17%, IO:DataFileExtend 0%.
- `CHECKPOINT_DURABILITY=exit`: 16 users, 1 → 4 replicas: throughput 59.4 → 77.5 incidents/s (×1.31), incident p95 309 → 283 ms.
- `CHECKPOINT_DURABILITY=exit`: 64 users, 1 → 4 replicas: throughput 62.8 → 68.5 incidents/s (×1.09), incident p95 1143 → 1492 ms.
- `CHECKPOINT_DURABILITY=exit`: one incident costs Postgres about 25 committed transactions and 28 inserted rows.
- `CHECKPOINT_DURABILITY=exit`: where busy Postgres sessions waited (4 replicas, 64 users, sampled mid-run): audit chain head row lock 94%, WAL flush (commit durability) 4%, Timeout:VacuumTruncate 1%.
- Postgres peaked at 110% CPU (100% is one core) in a Docker VM with 2 cores.

An *incident* is one create plus one approval, timed together. Closed-loop users: with more users than the stack can serve, latency is mostly queueing, so compare rows with the same number of users. The same cell varied by up to about 30% between runs hours apart on this machine, so compare rows from one run only and treat small gaps as noise.

## How this was run

- Machine: Apple M4, 10 logical cores, Darwin 25.5.0. Python 3.14.6, uvicorn 0.54.0.
- Replicas are `uvicorn` processes on that machine, one worker each, `EXECUTION_MODE=sync`, the offline rules provider (no model call), `RUNBOOK_STORE=postgres`. *Checkpoints* is `CHECKPOINT_DURABILITY`: `async` saves after every step (the default), `exit` only when a run finishes or pauses for approval.
- Postgres 17 (pgvector image, default durability settings) and Redis run in Docker (`compose.loadtest.yaml`, VM with 2 CPUs); the n8n webhook is a local stub that answers instantly.
- The load generator runs on the same machine and shares its cores with the replicas, so the numbers are for comparing replica counts, not absolute capacity.

Regenerate with `make loadtest` (needs Docker). Results vary with hardware; commit a new report only from a run you trust.
