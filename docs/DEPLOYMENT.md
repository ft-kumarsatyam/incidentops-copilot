# Deployment

IncidentOps runs as one API process (which also serves the built UI) plus,
optionally, n8n. This page covers running it for real.

## Choose a mode

| Mode | Command | Use for |
|---|---|---|
| Local dev | `make dev` (+ `make web-dev`) | Working on the code |
| Docker Compose | `docker compose up --build` | A demo, or a small team pilot on one host |
| Your own host | `incidentops serve --host 0.0.0.0` | Running under systemd or another supervisor |

## Environments and fail-closed auth

`INCIDENTOPS_ENV=dev` lets the API start without a key. Any other value requires
`INCIDENTOPS_API_KEY` or `OIDC_ISSUER`, and the process refuses to start without one. It also
refuses to start (API and workers) while a secret still holds a development placeholder
(`local-*`, `change-me`). Generate a key with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

People paste the key into **Settings** in the UI. It stays in that browser's session storage unless they tick "remember".

## Identity and access

Three kinds of credential identify a caller. All can be used at once.

| Credential | Sent as | Identity |
|---|---|---|
| SSO access token (OIDC) | `Authorization: Bearer <jwt>` | the token: user, roles and tenant claims |
| Scoped API key | `Authorization: Bearer iops_...` (or `X-API-Key`) | the key: its name, roles and tenant |
| Shared key (`INCIDENTOPS_API_KEY`) | `X-API-Key` | admin in the default tenant; `X-Actor` is an unverified label |

**Roles** are cumulative: `viewer` reads incidents and their trail; `responder` also opens
incidents, retries and sees the queue; `approver` also approves or rejects proposed actions;
`admin` also manages keys and searches, exports and verifies the audit log. Anything the AI or a
responder proposes waits for an approver.

**Scoped keys.**

```bash
incidentops keys create --name alerting --roles responder --tenant acme --expires-days 90
incidentops keys list --tenant acme
incidentops keys revoke <key-id>
```

The token is printed once; only a hash is stored. Admins can also use `POST /admin/keys`,
`GET /admin/keys` and `DELETE /admin/keys/{id}` for their own tenant. Give integrations such as
n8n a key with the least role they need.

**OIDC / SSO** (`pip install 'incidentops[oidc]'`; the image includes it). Set `OIDC_ISSUER` and
`OIDC_AUDIENCE`; keys are discovered from `{issuer}/.well-known/openid-configuration` or set
`OIDC_JWKS_URL`. Tokens must be signed with RS256/ES256 (never HMAC or `none`), carry the audience
and not be expired. Roles come from `OIDC_ROLES_CLAIM` (a list or space-separated string, dotted
paths such as `realm_access.roles` work), the tenant from `OIDC_TENANT_CLAIM` (missing means
`DEFAULT_TENANT`), and the name written to the audit log from `OIDC_ACTOR_CLAIM`. Map your IdP's
groups to the four role names in the IdP. A token with no recognised role can sign in and do
nothing. The UI does not run a browser login flow: paste a token in Settings, or put an
OIDC-aware proxy in front.

**Tenants.** Every incident and audit entry belongs to the tenant of the credential that created
it, and a caller never sees another tenant's data (another tenant's run is simply "not found").
Identical reports from two tenants become two incidents. The live stream is filtered per tenant.
`/metrics`, `/queue` counts and the CLI are operator-level and not tenant-scoped. Existing data
belongs to the `default` tenant.

**Approvals.** `FOUR_EYES_APPROVALS=2` with `FOUR_EYES_SEVERITIES=["SEV-1"]` requires two
different approvers, with a named identity (OIDC or scoped key), who agree on the actions; the
incident shows how many have approved, and one rejection is enough to reject. The shared key is
refused for these. `FORBID_SELF_APPROVAL=true` stops whoever opened an incident from approving it.

## Audit log

Entries form a SHA-256 hash chain per tenant, so editing, deleting or reordering a stored entry,
or dropping the newest ones, is detectable.

```bash
incidentops audit verify                         # all tenants; exit status 1 if tampered with
incidentops audit export --output audit.ndjson   # entries plus a chain check, for your SIEM
incidentops audit prune --days 365               # retention; the chain is re-anchored
```

Over HTTP (admins, own tenant): `GET /audit?event=decision&actor=…&since=…`, `GET /audit/verify`
and `GET /audit/export`. Authentication failures are recorded (reason, path, client, never the
credential) in a separate `_system` chain visible to operators through the CLI; forbidden
attempts, key changes and exports are recorded against the tenant. `AUDIT_READS=true` also logs
who opened each incident. Run `audit verify` on a schedule and alert on a non-zero exit.

The chain proves the log was not changed through the database's ordinary tables. Someone with full
database control could rewrite the log and its head together, so ship `audit export` regularly to
storage that the database operator cannot write to (an object-lock bucket, a SIEM). Entries
written before this feature existed are not chained.

## Secrets

| Secret | Held by | Notes |
|---|---|---|
| `INCIDENTOPS_API_KEY` | API, n8n, the browser | Rotate with the others when staff change |
| `N8N_ACTION_TOKEN` | API and n8n | Must match on both sides |
| `ZAPIER_ACTION_WEBHOOK_URL` | API | Treat the Catch Hook URL as a credential |
| `ZAPIER_ACTION_TOKEN` | API and Zap | Optional additional shared token |
| `ANTHROPIC_API_KEY` | API | Optional. Without it the rules engine runs |
| `GITHUB_TOKEN` | n8n (issue write), API (read, optional) | Use a fine-grained token on one repository |
| `SLACK_BOT_TOKEN` | n8n | The API never sees it |

Keep them in your platform's secret store. `.env` is git-ignored; `.env.example`
holds placeholders only. Any of them can be supplied as a file instead of a variable: set
`INCIDENTOPS_API_KEY_FILE=/run/secrets/api_key` (works for every plain-string setting, an explicit
variable wins), which fits Docker and Kubernetes secrets and a Vault agent that renders to disk.
Compose falls back to obvious `local-*` defaults and `INCIDENTOPS_ENV=dev` so `docker compose up`
works on a laptop; for anything else set real values and `INCIDENTOPS_ENV=prod`, and the API and
workers will refuse to start if a placeholder is left behind.

## Docker Compose

```bash
cp .env.example .env     # set INCIDENTOPS_API_KEY, N8N_ACTION_TOKEN, N8N_ENCRYPTION_KEY
docker compose up --build
```

- The API listens on `:8000` and has a health check; n8n waits for it.
- Data lives in the `incidentops-data` volume (`/app/.data`). n8n data lives in `n8n-data`.
- The n8n image is pinned (`N8N_IMAGE`). Upgrade it deliberately.
- The container runs as a non-root user.
- Runbooks are indexed at startup (`AUTO_INDEX_RUNBOOKS=true`).

For Zapier instead of n8n, set `ACTION_PROVIDER=zapier` and
`ZAPIER_ACTION_WEBHOOK_URL`; see [`zapier/README.md`](../zapier/README.md).

Put a TLS-terminating reverse proxy in front of both services. For the live
stream, do not buffer responses on `/incidents/stream`.

## Health and readiness

- `GET /health`: the process is up. Use it for liveness.
- `GET /ready`: database reachable and runbook index present, with the selected
  `action_provider` and `action_webhook` status. Returns `503` if not ready. Use it
  for load balancer checks.

## Logging and tracing

Logs are JSON on stdout (`LOG_JSON=true`, the default; `make dev` turns it off for readability), each line
carrying `request_id` and, inside a run, `run_id`. Ship them with your usual agent.

For traces, install the extra and point at a collector:

```bash
pip install -e ".[otel]"
OTEL_EXPORTER_OTLP_ENDPOINT=http://collector:4318 incidentops serve
```

## Backups

All state is in `DATABASE_PATH` (default `.data/incidentops.db`), plus WAL files
beside it. Use SQLite's online backup so the copy is consistent:

```bash
sqlite3 .data/incidentops.db ".backup 'backup.db'"
```

The runbook index is derived from `runbooks/` and can be rebuilt with
`incidentops runbooks index`.

## Capacity and limits

- **One API instance.** SQLite plus the in-process event bus assume it.
- `RATE_LIMIT_PER_MINUTE` is per client IP plus API key, and per process. A reverse proxy is the real defense.
- `MAX_BODY_BYTES` caps request size (default 256 KB).
- Claude calls are bounded by `AI_TIMEOUT_SECONDS` and `AI_MAX_OUTPUT_TOKENS`, and
  the critic loop by `MAX_RESEARCH_ATTEMPTS`. Per-run cost therefore has a ceiling.

## Runbook index at scale

The default index is SQLite FTS5 plus optional embeddings in the same file
(`RUNBOOK_RETRIEVAL=hybrid`, `pip install '.[rag]'`). That fits tens of runbooks
on one host.

When the corpus grows or several API instances need the same index, set
`RUNBOOK_STORE=postgres`. The index then lives in `DATABASE_URL` instead of a file per replica:

- **Lexical:** a generated `tsvector` column with a GIN index, ranked by `ts_rank_cd`. Needs
  nothing beyond the `postgres` extra.
- **Hybrid** (`RUNBOOK_RETRIEVAL=hybrid`, `pip install '.[postgres,rag]'`): adds a `vector(384)`
  column with an HNSW cosine index. Nearest-neighbour candidates and full-text candidates are
  scored together, so a query that shares no words with a runbook can still find it. This needs
  the pgvector extension: the `db` service in `compose.yaml` and the test services already use the
  `pgvector/pgvector:pg17` image; on a managed Postgres, enable the `vector` extension first.
- **Reindexing is safe to repeat and to race.** It is skipped when the corpus hash is unchanged,
  and the rewrite holds an advisory lock, so replicas that start together (`AUTO_INDEX_RUNBOOKS`)
  do not thrash the index. Run `incidentops runbooks index` after editing a runbook; with no
  change it is a no-op. `/ready` reports `runbook_index`.

The `RunbookRetriever` interface, query rewrite and grounding rules are unchanged, so triage
citations stay valid. Qdrant would be another store behind the same interface. Measured results:
[evals/retrieval_report.md](../evals/retrieval_report.md) (`pg-fts`, `pgvector`).

See [ADR 007](adr/007-hybrid-runbook-retrieval.md).

## Postgres

SQLite is the default and fine for one instance. For several instances or managed backups,
set `DATABASE_URL=postgresql://user:pass@host:5432/dbname` and install the extra
(`pip install 'incidentops[postgres]'`; the Docker image already includes it).

- The service fails fast at startup if the database is unreachable or a migration fails.
- `claim` (the approve/reject lock) is one atomic `UPDATE`, so two replicas cannot both decide.
- `DATABASE_POOL_SIZE` caps connections per instance (default 10).
- Existing SQLite data is not copied over. Start Postgres deployments fresh, or export the
  incidents you need to keep.
- `docker compose --profile postgres up` runs a bundled Postgres for local use.

## Redis

Set `REDIS_URL=redis://host:6379/0` and install the extra (`pip install 'incidentops[redis]'`;
the Docker image includes it). `REDIS_PREFIX` namespaces the keys, so several deployments can
share one Redis.

- **Event stream.** Events go through a Redis channel, and each replica relays them to its own
  connected clients. Delivery is a live hint: the database is the source of truth, and a
  replica that loses its Redis connection resubscribes with backoff. Events published during
  that gap are not replayed, so the UI's normal refresh covers them.
- **Rate limiting.** One sliding window per client, counted atomically in Redis (a Lua script
  using Redis' clock), so the limit holds across replicas.
- **Redis down.** The API keeps serving. Publishing delivers locally and the limiter counts per
  replica until Redis returns. `/ready` reports `redis: unavailable` but stays ready.
- `docker compose --profile redis up` runs a bundled Redis for local use. Combine it with
  `--profile postgres` for a two-replica setup.

## Async execution

By default `POST /incidents` runs the whole analysis before it answers. With
`EXECUTION_MODE=async` it stores the incident as `queued`, enqueues a job and answers
`202 Accepted` with `Location` and `Retry-After`. Clients poll `GET /incidents/{id}` or watch
`/incidents/stream`. Send `Prefer: wait=N` (capped by `MAX_WAIT_SECONDS`) to wait for the result
and get `200` if it finishes in time. Use that for n8n or any caller that needs the answer inline.

- **Queue.** A `jobs` table in the same database (migration 2), claimed with a single atomic
  update (`FOR UPDATE SKIP LOCKED` on Postgres). One job per run: re-posting an incident does not
  enqueue it twice.
- **Workers.** `WORKER_CONCURRENCY` threads inside the API, or separate processes with
  `incidentops worker [--concurrency N]` (compose: `--profile workers`, scale with
  `--scale worker=3`; set `WORKER_CONCURRENCY=0` on the API). On SIGTERM a worker stops taking
  jobs and lets running ones finish. Separate workers need a shared database (Postgres) and
  `REDIS_URL`, otherwise status changes they make do not reach the API's live stream.
- **Leases.** A running job holds a lease (`JOB_LEASE_SECONDS`) renewed by a heartbeat. If the
  worker dies, the lease expires and another worker takes the job and resumes the LangGraph run
  from its checkpoint, so nothing is analysed or dispatched twice.
- **Retries and dead letters.** A failed attempt is retried with exponential backoff (cap 60s) up
  to `JOB_MAX_ATTEMPTS`; each analysis is bounded by `RUN_TIMEOUT_SECONDS`. After the last attempt
  the job is `dead` and the incident is `errored`. `POST /incidents/{id}/retry-run` (the
  "Retry analysis" button) or `incidentops queue retry RUN_ID` gives it a fresh set of attempts.
- **Visibility.** `GET /queue` and `incidentops queue stats` show counts, the age of the oldest
  waiting job and recent dead jobs. `/ready` reports the same and degrades if jobs are dead.
- The Docker image includes the `llm` extra, so `ANTHROPIC_API_KEY` works out of the box.

## Scaling out

To run several API instances:

1. Set `DATABASE_URL` to a Postgres database. The read-model, audit log and LangGraph
   checkpoints (`PostgresSaver`) all move there. See [Postgres](#postgres).
2. Set `REDIS_URL`. The live event stream (SSE) and the rate limiter then use Redis, so a
   client connected to one replica sees changes made on another and limits are global. See
   [Redis](#redis).
3. Put a proxy or load balancer in front. Streams are long-lived, so allow idle timeouts above
   the 15 second keep-alive, and disable response buffering (the API already sends
   `X-Accel-Buffering: no`).
4. Set `EXECUTION_MODE=async` and run workers separately so a slow model call never ties up a
   request. See [Async execution](#async-execution).
5. Set `N8N_REDIS_URL` on every n8n instance, so the idempotency gate is one shared Redis claim instead of per-instance
   workflow static data. See [n8n/README.md](../n8n/README.md#idempotency).

### What scaling out buys you (measured)

[evals/loadtest_report.md](../evals/loadtest_report.md) (`make loadtest`) drives incident creation
and approval through 1, 2 and 4 replicas on one Postgres. On the machine in that report, going from
1 to 4 replicas raised throughput by about 20 to 30% and left p95 latency roughly flat. Replicas are
not the limit; the shared database is:

- One incident is about 37 committed transactions, mostly LangGraph checkpoint writes. Commit
  flushes (WAL) are the largest wait, so Postgres on fast local storage with a real core budget
  raises the ceiling far more than another API replica.
- Audit entries are chained per tenant, so writers of one tenant queue on a row lock held until
  commit. Load spread over several tenants does not queue on each other. A single busy tenant is
  capped by one commit at a time.
- Replicas still earn their place for availability, rolling restarts and for absorbing slow model
  calls (`EXECUTION_MODE=async` with separate workers), which this offline-rules test does not
  include.

Treat the absolute numbers as specific to that machine and re-run `make loadtest` on yours.

## Observability

Metrics are off by default. Set `METRICS_ENABLED=true` and install the extra
(`pip install 'incidentops[metrics]'`; the Docker image includes it) to serve Prometheus metrics at
`/metrics`.

- **Auth.** Outside `INCIDENTOPS_ENV=dev` the API refuses to start without `METRICS_TOKEN`, and the
  endpoint requires `Authorization: Bearer <token>`. The API key is not accepted there. `/metrics` is
  exempt from the rate limiter and is not counted in the HTTP metrics.
- **Cardinality.** Routes are labelled by template (`/incidents/{run_id}`), never by raw path, and no
  metric carries an incident id.
- **Per replica.** Each replica exposes its own counters. Scrape every replica and aggregate with
  `sum by (...)` in queries; the dashboard and alert rules already do.
- **Reference stack.** `docker compose --profile observability up --build` starts Prometheus
  (`:9090`) and Grafana (`:3000`, set `GRAFANA_ADMIN_PASSWORD`). Prometheus reads the scrape token
  from a file written at container start. For several replicas, replace the static target in
  [monitoring/prometheus.yml](../monitoring/prometheus.yml) with your service discovery.
- **Alerts.** [monitoring/alerts.yml](../monitoring/alerts.yml): replica down, 5xx above 5%, a failed
  dispatch, and p95 latency above 2 seconds. Route them with your own Alertmanager.
- **Traces and logs** are separate: `OTEL_EXPORTER_OTLP_ENDPOINT` enables traces, and logs are JSON
  with request and run ids.

| Metric | Labels |
|---|---|
| `incidentops_http_requests_total` | method, route, status |
| `incidentops_http_request_duration_seconds` | method, route |
| `incidentops_incidents_total` | status, severity |
| `incidentops_decisions_total` | decision |
| `incidentops_dispatch_total` | result (`sent`, `failed`, `not_configured`) |
| `incidentops_rate_limited_total` | |
| `incidentops_build_info` | version |

## Upgrades

Schema changes are versioned migrations (`src/incidentops/persistence/migrations.py`),
applied on startup and recorded in `schema_migrations`. On Postgres a session advisory lock
makes replicas that start together apply each migration once. Databases created before
migrations existed are adopted in place. Before an upgrade, take a backup.
