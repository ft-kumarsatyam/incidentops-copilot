# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions follow semver.

## [Unreleased]

### Added
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

### Fixed
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
