# IncidentOps HTTP contract

## Run incident

`POST /incidents`

Request fields: `run_id` (optional), `channel_id`, `thread_ts`, `reporter`,
`summary`, `service`, `owner`, `symptoms`, `monitoring`, and `github`.

Response:

```json
{
  "run_id": "inc-123",
  "idempotency_key": "inc-123",
  "status": "awaiting_approval",
  "severity": "SEV-1",
  "confidence": 1.0,
  "proposed_actions": ["Create SEV-1 incident record for checkout"],
  "missing_fields": [],
  "conflicts": [],
  "approval_required": true,
  "timeline": ["incident received", "research attempt 1"]
}
```

`needs_human` is a successful workflow response, not an HTTP failure.

## Submit decision

`POST /incidents/{run_id}/decision`

```json
{
  "idempotency_key": "inc-123",
  "decision": "approved"
}
```

Allowed decisions are `approved` and `rejected`. The service must reject a
mismatched `run_id`/idempotency key pair. An approved response returns the
action payload to n8n; it does not call Jira, GitHub, or Slack directly.

## Error behavior

- `400`: malformed or unsupported input
- `401/403`: caller authentication failure
- `409`: run/idempotency mismatch
- `503`: checkpoint or graph unavailable; n8n may retry with the same key

