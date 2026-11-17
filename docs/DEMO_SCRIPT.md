# Demo script (about 2 minutes)

Have the app running (`make dev`) with an empty database, and a second browser
tab ready on the same address.

## 0:00 The problem (15 s)

"Incident response starts as a half-written Slack message. People lose the first
minutes asking which service, how bad, and what changed. And if you let an AI
act on that, you want to be sure it cannot do anything you did not approve."

## 0:15 A clear incident (40 s)

Click **Load a sample incident**, pick **Checkout is failing**.

Narrate the pipeline as it moves. Then point at three things:

1. The evidence and the cited runbook: "Every proposal has to point at something."
2. The confidence and which engine produced it: "This ran offline on rules. With a key it uses Claude, and falls back if anything goes wrong."
3. The checklist: untick one action and press Approve.

Read the confirm dialog aloud: "It tells me exactly what will be sent."

## 0:55 Two people, one truth (15 s)

Switch to the second tab. It already shows the new status. "No refresh. Slack
and the browser see the same run."

## 1:10 When it should not decide (25 s)

Load **Something is wrong**. It lands in *Needs a human* with a list of what is
missing. "It stops rather than inventing an owner."

## 1:35 Proof (25 s)

```bash
make check
```

or show the last CI run. Say the numbers from the current run: Python tests,
UI tests, and the eval table (`evals/report.md`).

Close: "The model proposes, the commander approves, n8n executes. Every step is
logged, repeatable, and works with no API key at all."

## If something goes wrong

- Empty page with a hint: run `make web-build`.
- 401 in the UI: open **Settings** and paste `INCIDENTOPS_API_KEY`, or run with `INCIDENTOPS_ENV=dev`.
- No Slack or n8n: the demo does not need them. Dispatch reads `not_configured`.
