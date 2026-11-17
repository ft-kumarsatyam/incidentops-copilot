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
- `POST /webhook/incidentops/decision`

The decision path stores processed idempotency keys in workflow static data.
For production, replace static data with Redis or a database unique constraint.
See `docs/API_CONTRACT.md`.