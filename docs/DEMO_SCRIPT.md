# Demo script (90 seconds)

## 0–15s: problem

“Incident coordination starts as an incomplete Slack message. Humans waste time
asking for service, impact, owner, and recent changes before response begins.”

## 15–40s: successful run

```bash
workflow-run incident --fixture tests/fixtures/incident_complete.json
```

Show severity, evidence, proposed actions, `awaiting_approval`, and the stable
idempotency key. Explain that no external write happened.

## 40–65s: ambiguity and recovery

```bash
workflow-run incident --fixture tests/fixtures/incident_ambiguous.json
```

Show two research retries followed by `needs_human` with missing fields. Say:
“The agent stops rather than inventing an owner or service.”

## 65–80s: integration boundary

Open `n8n/incident-ops.json` in n8n. Point to Slack ingress, graph call,
approval, idempotency check, issue creation, and thread update.

## 80–90s: proof

```bash
pytest
python scripts/measure_impact.py
```

Closing line: “LangGraph owns bounded reasoning; n8n owns credentials and
actions; the incident commander owns the final decision.”