# ADR 010: Identity, roles, tenants and a tamper-evident audit log

**Status:** Accepted  
**Date:** 2026-11-18

## Context

One shared `X-API-Key` meant everyone who could call the API could approve production actions,
`X-Actor` was a free-text label anyone could forge, every caller saw every incident, and the audit
log was a plain table that could be edited without a trace. That is fine for one team's pilot and
not for an organisation with auditors.

## Decision

- **Identity.** Three ways in, resolved to one `Principal(subject, actor, tenant, roles)`:
  OIDC access tokens (JWT, validated against the provider's JWKS; asymmetric algorithms only, an
  audience is mandatory), scoped API keys (`iops_<id>_<secret>`, only a SHA-256 stored, with expiry
  and revocation) and the legacy shared key, which stays for compatibility and maps to admin in the
  default tenant. A wrong credential is always refused, even in open dev mode.
- **The actor is the identity.** For OIDC and scoped keys the audit actor comes from the token or
  key, and `X-Actor` is ignored. Only the legacy shared key still honours it.
- **RBAC.** `viewer < responder < approver < admin`, cumulative. Opening and retrying needs
  `responder`; approving needs `approver`, so the AI and the people who report only *propose*.
  Each route declares its permission; denials are audited.
- **Four-eyes and separation of duties.** `FOUR_EYES_APPROVALS=N` for chosen severities needs N
  different named approvers who agree on the actions; one rejection is enough. The shared key
  cannot supply them, since it cannot tell two people apart. `FORBID_SELF_APPROVAL` stops whoever
  opened an incident from approving it.
- **Tenancy.** `tenant_id` on incidents and audit entries, filtered in the repository, so a
  forgotten check in a route cannot leak data. Another tenant's run is *not found* (404). Ids that
  the server derives are salted with the tenant; a client-chosen `run_id` that another tenant
  already holds is a 409 that reveals nothing else. The live event stream filters by tenant.
- **Audit.** A SHA-256 chain per tenant, serialised by a row lock on the chain head so replicas
  cannot fork it. `verify` detects edits, deletions, reordering and removal of the newest entries.
  Search, NDJSON export, retention (`prune` re-anchors the chain) and logging of authentication
  failures and forbidden attempts complete it.
- **Secrets.** Outside dev the API and workers refuse to start with a `local-*` or `change-me`
  placeholder, and any secret can be read from a file (`NAME_FILE`) for Docker/Kubernetes secrets
  or a Vault agent.

## Consequences

- Everything is opt-in. With no keys and no OIDC configured, behaviour is as before (dev) or as
  with the shared key (everywhere else), and the 700+ existing tests still pass unchanged.
- Run ids differ between tenants for identical reports, which is the point.
- The hash chain proves the log was not changed through the database's tables. Someone with full
  control of the database can rewrite log and head together. Anchoring needs storage the operator
  cannot write, which is why `audit export` exists.
- Pruning deletes history by design. The chain stays verifiable from the new anchor.
- The browser UI sends whatever credential is pasted in Settings (scoped key or SSO token). A full
  browser login redirect (authorization code with PKCE) is not built; teams that need it put an
  OIDC-aware proxy in front or mint tokens in their portal.
- A person with both an OIDC login and a scoped key counts as two approvers for four-eyes. Use one
  mechanism for approvers.

## References

- [DEPLOYMENT.md](../DEPLOYMENT.md#identity-and-access), [SECURITY.md](../../SECURITY.md)
- [ADR 002](002-sqlite-checkpointer.md) for storage, [API contract](../API_CONTRACT.md)
