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
| Unauthenticated API access | A credential is required outside dev (SSO token, scoped key or shared key), constant-time comparison, startup refuses to run without one, failures are audited |
| A caller doing more than their role allows | RBAC (viewer, responder, approver, admin) enforced per route; proposing and approving are different roles; denials are audited |
| Forged identity in the audit log | For SSO tokens and scoped keys the actor comes from the verified identity; `X-Actor` is ignored for them |
| One person approving a SEV-1 alone | Optional four-eyes (`FOUR_EYES_APPROVALS`) with distinct named approvers; optional no-self-approval |
| Forged or confused tokens | OIDC: signature against the issuer's JWKS, issuer, mandatory audience, expiry; asymmetric algorithms only, so `none` and HMAC key-confusion tokens are refused |
| Leaked API key store | Only a SHA-256 of each scoped key's 256-bit secret is stored; keys expire and can be revoked |
| One tenant reading another's data | `tenant_id` filtered in the repository, another tenant's run is "not found", identical reports from two tenants stay separate, the live stream is filtered |
| Editing or deleting audit history | Per-tenant SHA-256 hash chain with a locked head; `incidentops audit verify` detects edits, deletions, reordering and truncation |
| Development placeholder secrets in production | Startup refuses `local-*` / `change-me` values outside dev; secrets can come from files
| Forged calls to n8n | Shared-secret Bearer token, checked in constant time before anything else; fails closed if unset |
| Approval via link preview or crawler | Decisions are POST only; Slack carries a link to the UI, never a state-changing URL |
| Secrets in stored data | Secrets and email addresses are redacted before storage and before prompts |
| Abuse and oversized input | Rate limit, body-size cap, field length limits, bounded model output and loop |
| Cross-site access | CORS is an allowlist, empty by default |

## Known limitations

- **The legacy shared key is still accepted.** It is admin in the default tenant and cannot tell
  people apart (`X-Actor` is unverified). Use scoped keys or SSO for anything shared, and remove
  the shared key where you can. Four-eyes approvals refuse it.
- **The audit chain is tamper-evident, not tamper-proof.** Someone with full database control can
  rewrite the log and its head together. Export it regularly to storage the database operator
  cannot write to. Entries older than the feature are not chained.
- **No browser login flow.** The UI sends the SSO token or key you paste in Settings. For a
  redirect-based login put an OIDC-aware proxy in front.
- **Authentication failures write to the audit log**, bounded by the rate limiter. A flood from
  many addresses can still grow it; alert on `auth:denied` volume and prune.
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

1. Set `INCIDENTOPS_ENV=prod`, use SSO or scoped keys with least-privilege roles, and set strong
   secrets (the service refuses placeholders).
2. Serve over HTTPS behind a reverse proxy; do not buffer `/incidents/stream`.
3. Set `CORS_ORIGINS` only if the UI is served from a different origin.
4. Scope the GitHub token to issues on one repository. Keep Slack and GitHub
   tokens in n8n only.
5. Verify Slack signatures on the intake webhook.
6. Back up the database and restrict access to it.
7. Review the audit log (`GET /incidents/{id}/audit`) as part of incident reviews.
8. Schedule `incidentops audit verify` and alert on a non-zero exit; export the log off the
   database host.
9. For SEV-1, set `FOUR_EYES_APPROVALS=2` and `FORBID_SELF_APPROVAL=true` with named identities.
