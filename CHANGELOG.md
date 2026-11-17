# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions follow semver.

## [Unreleased]

### Added
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
