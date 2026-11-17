# Integrations

IncidentOps keeps **write credentials in n8n** (or Zapier). Python proposes;
humans approve; n8n executes.

## Slack intake and notify

Import [`n8n/incident-ops.json`](../n8n/incident-ops.json). See
[n8n/README.md](../n8n/README.md) for environment variables.

Flow: Slack → `incidentops/intake` → `POST /incidents` → summary + UI link in
thread → commander approves in the web UI.

## Email status (actions path)

After approval, the workflow can send a status email when configured in n8n:

| Variable (n8n) | Purpose |
|---|---|
| `NOTIFY_EMAIL_TO` | Commander or mailing list |
| `NOTIFY_FROM_EMAIL` | From address for `emailSend` |

The template adds an **Send Email Status** node after GitHub issue creation.
If `NOTIFY_EMAIL_TO` is empty, skip the node in your n8n instance.

## Email intake (template)

To open incidents from email, add a parallel n8n branch: email trigger →
normalize → same `POST /incidents` shape as Slack. Keep credentials in n8n only.

## MCP read tools (optional)

Enable research-time context from MCP **read** servers:

```bash
MCP_ENABLED=true
# JSON list, e.g. [{"name":"slack","command":"..."}]
MCP_SERVERS=[]
```

Copy [`.mcp.json.example`](../.mcp.json.example) for local IDE-style servers.
The in-process bridge in `tools/mcp/bridge.py` is non-fatal on failure.

Install optional client support:

```bash
pip install -e '.[mcp]'
```

MCP must **not** perform writes; dispatch stays on n8n.

## Public URLs

| Variable | Purpose |
|---|---|
| `INCIDENTOPS_PUBLIC_URL` | Link in Slack approval message |
| `INCIDENTOPS_URL` | API base URL as n8n sees it |