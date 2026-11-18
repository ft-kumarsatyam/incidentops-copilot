# HTTP API contract

Interactive docs are served by the running API at `/docs` (OpenAPI at
`/openapi.json`). This page states the behavior those schemas cannot.

## Conventions

- **Auth:** send `Authorization: Bearer <token>` with an SSO access token or a scoped key
  (`iops_...`), or `X-API-Key: <INCIDENTOPS_API_KEY>` (the legacy shared key). Outside
  `INCIDENTOPS_ENV=dev` the service refuses to start without a key or `OIDC_ISSUER`. `/health` and
  `/ready` are open. A missing or wrong credential gets `401 {"detail": "invalid credentials"}`;
  a valid one without the needed role gets `403`.
- **Roles:** `viewer` (read) < `responder` (open, retry, queue) < `approver` (decide) <
  `admin` (keys, audit search/export/verify). `GET /me` shows yours.
- **Tenants:** you only see your own tenant's incidents; another tenant's run is `404`.
- **Actor:** with an SSO token or scoped key the audit actor is the identity and `X-Actor` is
  ignored. Only the shared key honours `X-Actor` (unverified).
- **Request ID:** every response carries `X-Request-ID`. Send your own to
  correlate logs.
- **Errors:** JSON `{"detail": "..."}` for domain errors. Middleware errors use
  `{"error": "..."}`.
- **IDs:** `run_id` matches `^[A-Za-z0-9._:-]{1,128}$`.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness |
| `GET` | `/ready` | Readiness: database, runbook index, n8n configuration |
| `GET` | `/incidents` | List and search, newest first |
| `POST` | `/incidents` | Start a run (idempotent) |
| `GET` | `/incidents/stream` | Live updates as Server-Sent Events |
| `GET` | `/incidents/{run_id}` | One incident |
| `GET` | `/incidents/{run_id}/audit` | Audit trail |
| `POST` | `/incidents/{run_id}/decision` | Approve or reject |
| `POST` | `/incidents/{run_id}/retry-dispatch` | Resend a failed dispatch |
| `POST` | `/incidents/{run_id}/retry-run` | Re-queue a failed analysis |
| `GET` | `/queue` | Queue counts and your dead jobs (responder) |
| `GET` | `/me` | The authenticated identity, roles and permissions |
| `POST` / `GET` / `DELETE` | `/admin/keys`, `/admin/keys/{id}` | Manage scoped keys for your tenant (admin) |
| `GET` | `/audit` | Search the audit log: `run_id`, `event`, `actor`, `since`, `until`, `limit`, `offset` (admin) |
| `GET` | `/audit/verify` | Recompute the hash chain (admin) |
| `GET` | `/audit/export` | The log as NDJSON ending with a chain check (admin) |

### `GET /incidents`

Query parameters: `status` (repeatable), `q` (text search, max 200 characters),
`limit` (1 to 200, default 50), `offset`. The total match count is in the
`X-Total-Count` response header.

### `POST /incidents`

```json
{
  "run_id": "inc-123",
  "channel_id": "C123",
  "thread_ts": "1700000000.0001",
  "reporter": "U123",
  "summary": "Checkout API returns 5xx after the 14:02 deploy",
  "service": "checkout",
  "owner": "payments-oncall",
  "symptoms": ["error rate 12%"],
  "monitoring": {"error_rate": 0.12},
  "github": {"recent_deploy": "abc123"}
}
```

`run_id` is optional; the service generates one. Posting a `run_id` that already
exists returns the stored run and starts nothing. Secrets and emails in the
payload are redacted before storage.

Response (abridged):

```json
{
  "run_id": "inc-123",
  "idempotency_key": "inc-123",
  "status": "awaiting_approval",
  "severity": "SEV-1",
  "confidence": 0.92,
  "hypotheses": ["Regression in the 14:02 deploy"],
  "proposed_actions": ["Roll back deploy abc123"],
  "action_items": [
    {"type": "rollback", "description": "Roll back deploy abc123", "evidence": ["github.recent_deploy"]}
  ],
  "missing_fields": [],
  "conflicts": [],
  "runbook_hits": [{"path": "runbooks/checkout-api.md", "title": "Rollback", "excerpt": "..."}],
  "ai": {"provider": "rules", "fallback_reason": null},
  "ai_review": {"concerns": []},
  "approval": null,
  "action_dispatch": null,
  "timeline": ["incident received", "research attempt 1"]
}
```

`needs_human` is a successful response, not an HTTP error.

Statuses: `running`, `awaiting_approval`, `needs_human`, `succeeded`, `failed`,
`cancelled`. `busy` appears briefly while a decision or retry is in flight.

### Four-eyes approval

For severities in `FOUR_EYES_SEVERITIES`, `POST .../decision` with `approved` records one
approval and returns the incident still `awaiting_approval`, with
`approval_progress: {"required": 2, "approved_by": ["..."]}`. A different named approver who
chooses the same `approved_actions` completes it. A `rejected` decision from anyone acts at once.
`403` if the caller is the shared key (or opened the incident, with `FORBID_SELF_APPROVAL`).

### `POST /incidents/{run_id}/decision`

```json
{
  "idempotency_key": "inc-123",
  "decision": "approved",
  "approved_actions": ["Roll back deploy abc123"]
}
```

- `decision` is `approved` or `rejected`.
- `approved_actions` is optional. Omit it to approve every proposed action. When
  present it must be a non-empty subset of `proposed_actions`.
- On approval the service sends only the chosen actions to n8n. `action_dispatch`
  in the response is `sent`, `not_configured`, or `failed`.

### `POST /incidents/{run_id}/retry-dispatch`

Valid only for a run that was approved and whose dispatch `failed`. It re-sends
the same payload with the same idempotency key.

### `GET /incidents/stream`

`text/event-stream`. The first frame is `event: ready`. After that, one
`event: incident` frame whenever any run changes, with
`{"type": "incident", "run_id": "...", "status": "..."}` as JSON in `data`.
Clients refetch the incident they care about. A `: keep-alive` comment is sent every 15 seconds.
Browsers' built-in `EventSource` cannot send headers, so the UI reads the
stream with `fetch` instead.

## Status codes

| Code | Meaning |
|---|---|
| `200` | Success, including `needs_human` |
| `401` | Missing or wrong API key |
| `404` | Unknown `run_id` |
| `409` | Idempotency key mismatch, run not awaiting approval, invalid `approved_actions`, or nothing to retry |
| `413` | Body larger than `MAX_BODY_BYTES` |
| `422` | Request failed validation |
| `429` | Rate limit exceeded; honor `Retry-After` |
| `500` | Unexpected error; details are in the logs under the `X-Request-ID` |
| `503` | `/ready` only: a dependency is unavailable |

## The n8n actions webhook

When `N8N_ACTION_WEBHOOK_URL` is set, an approval makes the service send:

```http
POST <N8N_ACTION_WEBHOOK_URL>
Authorization: Bearer <N8N_ACTION_TOKEN>
Idempotency-Key: inc-123
Content-Type: application/json
```

The body is the full incident (the same fields `GET /incidents/{run_id}`
returns), with `approved_actions` set to the actions the commander chose. n8n
should act only on `approved_actions`, never on `proposed_actions`.

The service retries 5xx, 429 and transport errors with exponential backoff
(`N8N_DISPATCH_ATTEMPTS`, `N8N_DISPATCH_BACKOFF`). n8n verifies the token and
drops a repeated idempotency key, so a retry cannot create a second ticket.

## The Zapier actions webhook

With `ACTION_PROVIDER=zapier`, approvals are sent to a Webhooks by Zapier Catch
Hook:

```http
POST <ZAPIER_ACTION_WEBHOOK_URL>
Idempotency-Key: inc-123
X-IncidentOps-Event: incident.actions_approved
X-IncidentOps-Token: <ZAPIER_ACTION_TOKEN>
Content-Type: application/json
```

The JSON has flat mapping fields plus the complete incident under `incident`.
Zapier must use Storage by Zapier to claim `idempotency_key` before external
actions. Retry behavior is controlled by `ZAPIER_DISPATCH_ATTEMPTS` and
`ZAPIER_DISPATCH_BACKOFF`. See [`zapier/README.md`](../zapier/README.md).
