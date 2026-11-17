# Resume and portfolio

## Project line

**IncidentOps Copilot** — Slack-native LangGraph workflow that investigates
engineering incidents, retries missing evidence, and requires human approval
before n8n executes Jira/GitHub actions.

## Resume bullets

Replace bracketed values only after running a pilot:

- Reduced median incident coordination time from **[X] to [Y] minutes** by
  automating Slack intake, GitHub/monitoring context collection, severity
  assessment, and ticket creation through LangGraph and n8n.
- Built a cyclic five-node agent workflow with persisted `run_id` checkpoints,
  two-attempt evidence recovery, and explicit human escalation, correctly
  routing **[X%]** of ambiguous synthetic incidents without unsafe writes.
- Prevented duplicate tickets from Slack webhook retries using idempotency keys
  and separated the reasoning plane (Python) from the credential/action plane
  (n8n).

## Interview proof points

- **Why a graph?** The critic can route back to research and checkpoint before
  approval; a linear prompt obscures both behaviors.
- **Why n8n?** It owns SaaS credentials, webhook retries, and approval UI while
  Python remains testable and vendor-neutral.
- **Where is the agentic behavior?** Intake decomposes missing information,
  research uses tools, assessment proposes actions, and critic either loops or
  escalates.
- **What happens when tools fail?** Partial evidence is preserved and the run
  becomes human-owned; the system does not manufacture context.
- **How is impact measured?** `scripts/measure_impact.py` prints assumptions,
  coordination steps removed, and time saved for a reproducible pilot claim.

## Portfolio checklist

- Record the complete fixture ending `awaiting_approval`.
- Record the ambiguous fixture looping twice and ending `needs_human`.
- Show the n8n workflow and duplicate-delivery idempotency test.
- Link the architecture and include actual pilot assumptions.

## Honest metric language

Before production use:

> Validated on [N] synthetic incidents; automated [X/Y] coordination steps and
> escalated all fixtures missing required ownership or service context.

After a team pilot:

> Reduced median time-to-acknowledge by [X%] across [N] incidents while keeping
> all external writes behind incident-commander approval.

Keywords: Python, LangGraph, agentic workflow, tool calling, Slack API, GitHub
API, n8n, human-in-the-loop, idempotency, checkpoints, incident management.