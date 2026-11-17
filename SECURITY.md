# Security

## Reporting a vulnerability

Please do not open a public issue. Use GitHub's
[private vulnerability reporting](https://github.com/ft-kumarsatyam/incidentops-copilot/security/advisories/new)
for this repository. Include steps to reproduce and the version or commit.
You can expect an acknowledgement within a few days. This is a small project
maintained by one person, so there is no formal SLA.

## Threat model

IncidentOps reads untrusted text (Slack messages, tool output), asks a model to
reason about it, and can cause a write to GitHub and Slack. The design assumes
the text is hostile and the model can be wrong.

| Threat | Mitigation |
|---|---|
| Prompt injection in an incident report | Text is fenced in an `<incident>` block with `<` escaped; the model has no tools that write; its output is restricted to a closed action list and checked in code |
| The model invents an action or a fact | Every action must cite evidence or a runbook; rollback needs a deploy; severity is checked against measurements |
| An unapproved write | Writes happen only in n8n, only after `approved` through the API, and only for the chosen subset of actions |
| Double submit or replay | Idempotency keys, an atomic claim on the run, and an n8n gate marked done only after success |
| Unauthenticated API access | `X-API-Key` required outside dev, constant-time comparison, startup refuses to run without a key |
| Forged calls to n8n | Shared-secret Bearer token, checked in constant time before anything else; fails closed if unset |
| Approval via link preview or crawler | Decisions are POST only; Slack carries a link to the UI, never a state-changing URL |
| Secrets in stored data | Secrets and email addresses are redacted before storage and before prompts |
| Abuse and oversized input | Rate limit, body-size cap, field length limits, bounded model output and loop |
| Cross-site access | CORS is an allowlist, empty by default |

## Known limitations

- **Single shared API key.** There are no per-user accounts. `X-Actor` names who
  acted in the audit log but is only as trustworthy as the key.
- **Slack signatures are not verified** in the template workflow. Add this before
  exposing the intake webhook.
- **The API key lives in the browser** once entered in Settings: in session
  storage by default, in local storage if the user ticks "remember". Treat the
  UI as an internal tool, and serve it over HTTPS.
- **The rate limiter is per process**, keyed by client IP and API key.
- **Redaction is pattern based.** It catches common token shapes and emails, not
  every possible secret.
- **n8n's idempotency gate uses workflow static data**, fine for one instance.
- SQLite is unencrypted at rest. Use disk encryption and restrict file permissions.

## Hardening checklist

1. Set `INCIDENTOPS_ENV` to something other than `dev` and set strong keys.
2. Serve over HTTPS behind a reverse proxy; do not buffer `/incidents/stream`.
3. Set `CORS_ORIGINS` only if the UI is served from a different origin.
4. Scope the GitHub token to issues on one repository. Keep Slack and GitHub
   tokens in n8n only.
5. Verify Slack signatures on the intake webhook.
6. Back up the database and restrict access to it.
7. Review the audit log (`GET /incidents/{id}/audit`) as part of incident reviews.
