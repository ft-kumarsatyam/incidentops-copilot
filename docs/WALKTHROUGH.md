# IncidentOps walkthrough

## 1. Run the deterministic demo

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
workflow-run check
workflow-run incident --fixture tests/fixtures/incident_complete.json
```

The result should be `awaiting_approval`. No external system is changed.

## 2. Observe ambiguity recovery

```bash
workflow-run incident --fixture tests/fixtures/incident_ambiguous.json
```

The critic routes to research twice. With required facts still missing, the
run ends as `needs_human` and includes exactly what a responder must supply.

## 3. Understand the nodes

1. `intake` validates trust-boundary input.
2. `research` normalizes supplied GitHub and monitoring evidence.
3. `assess` computes a transparent baseline severity and action proposal.
4. `critic` checks evidence and controls the bounded loop.
5. `finalize` writes a checkpoint and returns an approval/escalation payload.

The baseline is intentionally deterministic. Replace `assess` with an LLM
structured output only after these graph contracts and tests remain green.

## 4. Connect n8n and Slack

1. Import `n8n/incident-ops.json`.
2. Configure Slack and an `INCIDENTOPS_URL` credential/environment value.
3. Point the Python endpoint or wrapper at `run_incident`.
4. Post a Slack app mention with service, symptom, and observed impact.
5. Verify the approval message appears in the same thread.
6. Approve once and verify only one issue is created.
7. Replay the webhook and verify the idempotency key prevents duplication.

The checked-in workflow is a portable contract/template. Credential IDs and
the final Jira/GitHub action are selected in the target n8n instance.

## 5. Failure drills

| Fixture | Expected |
|---|---|
| complete incident | `awaiting_approval`, severity and actions present |
| missing owner/service | two retries, then `needs_human` |
| monitoring timeout | partial evidence, explicit warning |
| conflicting severity | human escalation |
| duplicate delivery | same `run_id` and idempotency key |

## 6. Measure before claiming impact

```bash
python scripts/measure_impact.py \
  --manual-minutes 20 \
  --automated-minutes 4 \
  --incidents 25
```

Use observed values from a pilot. The script prints assumptions alongside the
result so a portfolio claim remains reproducible.

## 7. Production hardening after the MVP

- Put the graph behind an authenticated HTTP endpoint.
- Replace local JSON checkpoints with a durable LangGraph checkpointer.
- Verify Slack signatures and enforce n8n credential scoping.
- Add runbook retrieval only when a real runbook corpus exists.
- Add model evaluation only when an LLM replaces deterministic assessment.