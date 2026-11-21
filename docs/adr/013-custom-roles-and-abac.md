# ADR 013: Custom roles per tenant and attribute rules on approval

**Status:** Accepted  
**Date:** 2026-11-21

## Context

Four cumulative roles (viewer, responder, approver, admin) were fixed in code. Real teams need
"the payments team approves payments incidents only" and "only senior staff approve a rollback".
Neither fits a ladder of four roles. [ADR 010](010-identity-and-governance.md) set the identity
model; [ADR 012](012-target-architecture-roadmap.md) listed this as a Tier 2 item.

## Decision

- **Custom roles live in the database**, per tenant (`roles` table, migration 4): a name, a list of
  permissions and an optional list of service glob patterns. Admins manage them with
  `PUT/GET/DELETE /admin/roles`, guarded by the new `roles:manage` permission, and every change is
  audited (`role:created`, `role:updated`, `role:deleted`).
- **The four built-in roles stay in code** and cannot be redefined. A custom role never *removes*
  anything: a caller's permissions are the union of their built-in role and the grants of their
  custom roles. Built-in roles apply to every service.
- **Roles are resolved on every request**, not baked into the credential. A key or token only
  carries role *names*; the grants are looked up in the caller's tenant when they authenticate.
  Editing or deleting a role therefore takes effect on the next request, with no cache to
  invalidate. An unknown or deleted name grants nothing. The cost is one indexed query per
  request that carries a custom role; the cache and revocation item in the roadmap would remove it.
- **Two attribute rules on approval** (`IncidentService.decide`), applied to named identities only:
  1. *Service scope.* Approving or rejecting needs `incident:decide` *for the incident's service*
     (`Principal.can_on`). A custom role limited to `pay-*` cannot decide `checkout-api`.
  2. *Action risk.* Actions whose type is in `HIGH_RISK_ACTIONS` (empty by default; for example
     `["rollback","scale"]`) need `incident:decide_high_risk` on top, which approvers have
     built in. Only approvals are checked: rejecting is always safe.
  Both denials are `403` and audited as `auth:forbidden` with the reason.
- **Role names in tokens.** OIDC role claims now pass any name that looks like a role name through.
  A token cannot grant a permission this way: the name only matters if the tenant defined it.
  Keys are stricter: creating a key with a role the tenant has not defined is a `422`.

## Consequences

- The shared key and anonymous dev mode are admins and are not subject to scope or risk rules.
- A role's services apply to `incident:decide` only. Reading and creating are not scoped by
  service; doing that would need the same filter in list and search queries, which is a larger
  change.
- Severity-based rules are still the existing four-eyes setting. Row-level security in Postgres
  remains a separate roadmap item.
- Deleting a role does not delete keys that carry its name; they keep working with whatever their
  other roles give.
