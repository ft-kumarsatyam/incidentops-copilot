# Zapier adapter

IncidentOps can dispatch approved actions to a **Webhooks by Zapier — Catch
Hook** trigger. n8n remains the default.

## Configure outbound approved actions

1. Create a Zap with **Webhooks by Zapier → Catch Hook**.
2. Copy its hook URL into `ZAPIER_ACTION_WEBHOOK_URL`.
3. Set `ACTION_PROVIDER=zapier`.
4. Optionally set `ZAPIER_ACTION_TOKEN`; IncidentOps sends it as
   `X-IncidentOps-Token`.
5. Restart the API and approve a test incident.

The hook URL is a credential. Store it as a secret and never commit it.

## Required idempotency steps

Zapier Catch Hooks do not deduplicate deliveries. Before Jira, Linear, email,
or Slack actions:

1. Look up `idempotency_key` with **Storage by Zapier**.
2. Continue only when the key is absent.
3. Store the key before the external write.

IncidentOps retries `429`, `5xx`, and transport failures, so this gate is
required.

## Event fields

The adapter sends a flat event for easy field mapping:

- `event`: always `incident.actions_approved`
- `idempotency_key`, `run_id`
- `status`, `severity`, `service`, `summary`, `owner`
- `approved_actions`: the human-approved subset
- `incident`: the complete state for advanced steps

See [`action-payload.example.json`](action-payload.example.json).

## Optional inbound incidents

A separate Zap can turn alerts, forms, or email into incidents:

1. Choose any Zapier trigger.
2. Add **Webhooks by Zapier → Custom Request**.
3. `POST` JSON to `<INCIDENTOPS_URL>/incidents`.
4. Add `X-API-Key: <INCIDENTOPS_API_KEY>`.
5. Map `channel_id`, `thread_ts`, `reporter`, `summary`, `service`, `owner`,
   `symptoms`, `monitoring`, and `github`.

Writes still require a later human approval; inbound automation cannot bypass
the workflow gate.

