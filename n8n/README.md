# n8n workflow

Import `incident-ops.json` through **Workflows → Import from file**.

Required environment values:

- `INCIDENTOPS_URL`: authenticated Python service wrapper
- `N8N_PUBLIC_URL`: public n8n base URL
- `SLACK_BOT_TOKEN`: bot allowed to post in the incident channel
- `GITHUB_TOKEN`: issue-write token scoped to one repository
- `GITHUB_REPO`: `owner/repository`

The workflow has two webhook paths:

- `POST /webhook/incidentops/intake`
- `GET /webhook/incidentops/decision`
- `POST /webhook/incidentops/actions` (called by the approved API decision)

The decision and action paths store separate processed idempotency keys in
workflow static data. Set the API's `N8N_ACTION_WEBHOOK_URL` to the actions
path so dashboard approvals use the same execution flow as Slack.
For production, replace static data with Redis or a database unique constraint.
See `docs/API_CONTRACT.md`.