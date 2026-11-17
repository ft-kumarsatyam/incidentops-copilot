# Changelog

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions follow semver.

## [Unreleased]

### Added
- Documentation set: README rewrite, architecture, API contract, deployment, security,
  and ADRs 002 to 005.
- `Mark Action Done` node in the n8n workflow, and Node-executed tests for the gate and
  the token check.
- Dependabot tracking for the web UI's npm dependencies.

### Fixed
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
