# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions follow semver.

## [Unreleased]

### Added
- Custom roles per tenant (`roles` table, migration 4; `PUT/GET/DELETE /admin/roles`, permission `roles:manage`)
  and attribute rules on approval: a role limited to some services (`checkout-*`) can decide only those,
  and `HIGH_RISK_ACTIONS` need the new `incident:decide_high_risk` permission. Keys and SSO tokens carry
  role names that are resolved on every request. See ADR 013.
- Dispatch outbox: approving records its intent to dispatch (a `dispatch` job in the existing
  queue) before resuming the run, so a crash between approving and sending is recovered by a worker
  or the next start instead of silently losing the send (`DISPATCH_OUTBOX`,
  `DISPATCH_RECOVERY_SECONDS`). New job-store methods `arm`, `finish` and `expedite`.
- Circuit breakers (`BREAKER_FAILURE_THRESHOLD`, `BREAKER_RESET_SECONDS`) around the model, Jev and
  the action webhook, exported as `incidentops_circuit_*` metrics, plus `incidentops_jev_*` counters.
- `Idempotency-Key` header on `POST /incidents`: a retry returns the same run (`Idempotent-Replayed`),
  a reused key with a different report is a 409. Works across replicas, no extra store.
- SSRF guard for the action webhook (`core/ssrf.py`): link-local and unspecified addresses always
  refused, `OUTBOUND_BLOCK_PRIVATE` for loopback and private ranges.
- Role-aware UI: the Report link, samples and report page are hidden from roles that cannot create
  incidents, and the header shows your role and tenant.
- `docs/END_TO_END_ARCHITECTURE.md` (context, components, request sequence, RBAC, data model,
  deployment, failure modes, security, checklist coverage and a tiered roadmap) and ADR 012.
- Jev (TypeSafe System One) decision model as an advisor: `JEV_ENABLED`, `JEV_API_KEY`, `POST /decisions`
  and `GET /decisions/status`, with new RBAC permissions `decision:invoke` (responder) and
  `decision:admin` (admin), a tenant allow-list, and audited calls. In triage it only adds conflicts
  or flags; see ADR 011.
- `CHECKPOINT_DURABILITY` (`async` default, `sync`, `exit`): when LangGraph saves workflow
  checkpoints. `exit` saves only when a run finishes or pauses for approval, which cuts the
  database commits per incident (measured in the load test) at the cost of restarting a run from
  the beginning if its worker dies mid-run. About 1.5× throughput in the load test.
- Load test (`make loadtest`, `scripts/loadtest.py`, `compose.loadtest.yaml`): creates and approves
  incidents through 1, 2 and 4 replicas on a shared Postgres and Redis, and writes p50/p95/p99,
  throughput, errors, Postgres CPU and database wait events to `evals/loadtest_report.md`.
- Shared runbook index in Postgres (`RUNBOOK_STORE=postgres`): a `tsvector` + GIN full-text index,
  and for `RUNBOOK_RETRIEVAL=hybrid` a pgvector `vector(384)` column with an HNSW cosine index that
  also contributes candidates the full-text search missed. Reindexing is skipped when the corpus is
  unchanged and runs under an advisory lock, so replicas starting together do not race. `/ready`
  reports `runbook_index`. The retrieval eval gains `pg-fts` and `pgvector` modes, and the compose
  and CI Postgres images are now `pgvector/pgvector:pg17`.
- n8n workflow: opt-in Slack signature verification (`SLACK_SIGNING_SECRET`, with replay window and
  `url_verification` support) and a Redis idempotency claim (`N8N_REDIS_URL`: `SET NX` lease,
  released on failure, shared by several n8n instances). The workflow's JavaScript now lives in
  `n8n/src/` and `make n8n-build` / `make n8n-check` keep the JSON in sync. `make e2e-n8n` runs two
  real n8n containers against one Redis.
- Identity and governance ([ADR 010](docs/adr/010-identity-and-governance.md)). Callers sign in with
  an OIDC access token (JWKS-validated; asymmetric algorithms only; audience required), a scoped API
  key (`incidentops keys create|list|revoke`, `/admin/keys`; hashed at rest, expiring, revocable) or
  the legacy shared key. The audit actor now comes from the verified identity, and `X-Actor` is
  ignored for tokens and scoped keys. Role-based access control: viewer, responder, approver, admin,
  enforced per route, with denials audited; `GET /me` reports yours and the UI hides actions you
  cannot take. Four-eyes approval (`FOUR_EYES_APPROVALS`, `FOUR_EYES_SEVERITIES`) needs distinct
  named approvers who agree on the actions, and `FORBID_SELF_APPROVAL` stops whoever opened an
  incident from approving it.
- Multi-tenancy: `tenant_id` on incidents and audit entries, filtered in the repository. Another
  tenant's run is "not found", identical reports from two tenants stay separate, and the live stream
  is filtered per tenant. Existing data belongs to the `default` tenant.
- Tamper-evident audit log: a per-tenant SHA-256 hash chain with a locked head. `incidentops audit
  verify|export|prune`, `GET /audit` (search), `/audit/verify`, `/audit/export` (NDJSON), retention
  that re-anchors the chain, and audit entries for authentication failures, forbidden attempts, key
  changes, exports and (optionally, `AUDIT_READS`) reads.
- Secrets handling: outside dev the API and workers refuse to start with `local-*` / `change-me`
  placeholder secrets, any plain-string setting can be read from `NAME_FILE` (Docker/Kubernetes
  secrets, Vault agent), and compose defaults to `INCIDENTOPS_ENV=dev` explicitly.
- Migration 3 (identity and governance). New `oidc` extra (`pyjwt[crypto]`), included in the image.
- Async execution (opt-in, `EXECUTION_MODE=async`): `POST /incidents` answers `202` with
  `Location`/`Retry-After` (or the result, with `Prefer: wait=N`), and workers run the analysis from
  a durable database-backed queue with leases and heartbeats, retries with backoff, a run timeout, a
  dead state (`errored` incident) and idempotent enqueue. A killed worker's job is taken over after
  the lease expires and resumes from its checkpoint. `incidentops worker`, `incidentops queue
  stats|retry`, `GET /queue`, `POST /incidents/{id}/retry-run`, a compose `workers` profile and a
  "Retry analysis" action in the UI. New statuses: `queued`, `errored`. SQLite and sync setups are
  unchanged.
- Prometheus metrics (opt-in, `METRICS_ENABLED`) at `/metrics`, protected by `METRICS_TOKEN` and
  fail-closed outside dev: HTTP requests and latency by route template, incidents by outcome and
  severity, decisions, dispatch results, rate-limited requests and build info. Install with the
  `metrics` extra.
- `docker compose --profile observability`: Prometheus with alert rules (replica down, 5xx, failed
  dispatch, slow requests) and Grafana with a provisioned IncidentOps dashboard.
- README badges for eval results and test count are generated by `scripts/update_badges.py` from the
  eval reports (`make badges`); CI fails a pull request whose badges are stale, and a workflow
  refreshes them on main.
- GitHub Codespaces dev container, a tagged-release workflow that publishes the container image to
  GHCR, condensed README translations (简体中文, हिन्दी, Español) and a multi-replica topology diagram.
- `make test`, `make cov` and `make check` start a throwaway Postgres and Redis
  (`compose.test.yaml`) and run in strict mode (`TESTS_STRICT=1`), where a missing service or
  optional extra fails instead of skipping. CI runs strict too and installs the `rag` extra, so
  the hybrid-retrieval test runs there. `make test-fast` runs without Docker.
- Any Python warning now fails the test suite. The only exemption is the Python 3.14
  `asyncio.iscoroutinefunction` deprecation inside `langsmith`, which has no upstream fix yet.

- Redis (`REDIS_URL`) for the live event stream and rate limiting, so API replicas share both.
  The event bus publishes through a Redis channel with an auto-reconnecting listener; the rate
  limiter is an atomic sliding window in Redis. Both fall back to in-process behaviour if Redis
  is unreachable, and `/ready` reports its state. SQLite-only, single-instance setups are
  unchanged. Install with the `redis` extra.
- Postgres backend (`DATABASE_URL`) for the incident read-model, audit log and LangGraph
  checkpoints, with a pooled connection, fail-fast startup, and an atomic approval claim.
  SQLite stays the default. Install with the `postgres` extra.
- Versioned schema migrations (`schema_migrations`) for both backends, with a Postgres advisory
  lock so replicas starting together apply each migration once.
- CI runs the repository and service tests against a Postgres service container; the same
  contract tests run on SQLite and Postgres.
- Webhook security (opt-in): HMAC-SHA256 signing with replay protection for dispatches to n8n and
  Zapier (`WEBHOOK_SIGNING_SECRET`), signature check on `POST /incidents`
  (`INBOUND_SIGNING_SECRET`), and an outbound host allowlist (`OUTBOUND_ALLOWED_HOSTS`).
- Guardrail layers: output content policy (destructive commands, remote code, exfiltration,
  markup, injection echo, unsanctioned escalation targets), per-type evidence rules, a stricter
  severity cross-check, an input injection scanner (`guardrail_flags`), critic-output validation,
  and allowlisted GitHub and monitoring input. See `docs/GUARDRAILS.md`.
- `make eval-guardrails`: scripted hostile and benign model outputs with gated attack block rate
  and false-positive rate, plus 27 end-to-end edge-case incidents (`evals/dataset/guardrails/`)
  and new scorer checks (`bounded`, `no_leak`, `flagged`, `calibration`, `actions`).
- About 250 new tests, including mutation tests that prove the evals fail when a guardrail is
  removed.
- Redaction for card numbers, SSNs, PEM keys, JWTs, Google and Stripe keys, Basic auth and URL
  credentials.
- `docs/PROJECT_BRIEF.md` for engineering leaders, and README screenshots.
- Documentation set: README rewrite, architecture, API contract, deployment, security,
  and ADRs 002 to 005.
- `Mark Action Done` node in the n8n workflow, and Node-executed tests for the gate and
  the token check.
- Dependabot tracking for the web UI's npm dependencies.

### Removed
- The `workflow-run` command alias. Use `incidentops`.
- Personal career-positioning docs and the stale benchmark notes (still in git history).

### Changed
- `INCIDENTOPS_API_KEY` is no longer the only way in: production needs it **or** `OIDC_ISSUER`.
  An incident's stored state now includes `tenant_id`, and live events carry a `tenant` field.
- The CORS allow-list now includes `Authorization` and `Prefer`.

### Fixed
- Replicas starting at the same moment on an empty Postgres database could crash or deadlock while
  creating LangGraph's checkpoint tables. Setup now runs under an advisory lock that waiters poll
  for (a blocking lock would deadlock against LangGraph's `CREATE INDEX CONCURRENTLY`). Found by the
  load test.
- The n8n webhooks had no `webhookId`, so importing the workflow with the n8n CLI or API served them
  at `/webhook/<workflowId>/<node>/<path>` instead of the documented `/webhook/incidentops/...`.
- A single open live-event stream (one browser tab) made `make dev` hang for as long as the tab was
  open: on a reload uvicorn stopped answering but waited forever for the stream to close, so requests
  such as creating an incident were accepted and never answered. `incidentops serve` and the Docker
  command now bound graceful shutdown (`SHUTDOWN_TIMEOUT_SECONDS`, default 5; the image uses 10), so a
  reload or a rolling restart finishes in seconds. The client already reconnects.
- The Docker image did not include the `anthropic` SDK, so every analysis failed when
  `ANTHROPIC_API_KEY` was set. The image now installs the `llm` extra.
- The evals logged about 150 lines of guardrail and incident events into their output because
  they never configured logging. They now run quiet.
- Unclosed SQLite connections in the migration tests.
- A non-numeric `error_rate` such as `"12%"` crashed triage, and the rules fallback crashed too.
  It is now parsed, or marks impact unverified.
- A NaN model confidence was clamped to 1.0 and could pass the approval threshold. It now
  fails closed to 0.0.
- Email and URL-credential redaction was quadratic: a 250 KB body took minutes of CPU.
- A `repo` value from the incident was interpolated into the GitHub API path unvalidated.
- A `rollback` action could ground itself on the report alone.
- The web CI job failed on a clean install because `@types/node` was not declared; a copy in
  a parent directory hid it locally.
- Runbook search now falls back to no results for an index built by an older version. The
  previous handler missed `sqlite3.InterfaceError`, so a stale index crashed research.

### Changed
- n8n actions are authenticated with a shared Bearer token (`N8N_ACTION_TOKEN`), and the
  decision callback is POST only. The Slack message links to the UI instead of a decision URL.
- The n8n idempotency gate records an action as done only after the GitHub issue exists, so
  a failed attempt can be retried instead of being skipped as a duplicate.

## [0.2.0]

### Added
- React web UI with live updates (SSE), guided first run, per-action partial approval, and a
  confirm dialog (phase 3).
- LLM triage and critic agents with guardrails, a rules fallback and an eval harness (phase 2).
- Durable workflow on a SQLite checkpointer, resumable approvals, an audit log, idempotent
  start and decide, failed-dispatch retry, structured logging, `/ready`, rate limiting and
  fail-closed auth (phase 1).

### Changed
- Package renamed from `workflow_automation` to `incidentops`; CLI is now `incidentops`.

## [0.1.0]

- Initial deterministic LangGraph workflow, HTTP API, dashboard, runbook retrieval and Docker setup.
