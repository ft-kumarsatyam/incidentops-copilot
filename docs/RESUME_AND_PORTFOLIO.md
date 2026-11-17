# Resume and portfolio

## Project line

**IncidentOps Copilot**: an incident-triage assistant. Claude (with a deterministic
fallback) proposes grounded actions, a LangGraph workflow waits for a person's
approval, and n8n performs the writes.

## What is true today

Use these as facts. They come from the repo, not from a pilot.

- 110+ Python tests at 94% coverage, 48 UI tests, mypy and ruff clean.
- 10 labeled incidents scored on every pull request. The offline baseline passes 10 of 10
  (`evals/report.md`). Claude has not been scored yet.
- Durable runs: approval is a LangGraph `interrupt()` on a SQLite checkpointer; a run
  survives a restart.
- Safe repeats: idempotent start and decide, an atomic claim so two approvals cannot both
  proceed, retry with backoff on dispatch, and a `failed` state with a retry action.
- Live UI over Server-Sent Events with polling fallback.

## Resume bullets

Pick two or three. Keep the numbers you can reproduce.

- Built an incident-triage system where an LLM proposes actions and a human approves
  them: LangGraph workflow with a SQLite checkpointer and `interrupt()`-based approval,
  FastAPI service, React and TypeScript UI with live updates, and n8n for Slack and GitHub.
- Designed code-level guardrails around the model (closed action list, evidence grounding,
  severity-versus-measurement checks, confidence ceiling) with a rules fallback, so the
  system works offline and degrades safely when the model fails or is attacked.
- Created an eval harness of 10 labeled incidents, including a prompt-injection case,
  scored in CI on every pull request. The offline baseline passes 10 of 10.
- Made approvals safe to repeat: idempotency keys end to end, atomic run claims, retrying
  dispatch with backoff, and an audit log. Found and fixed a case where a failed GitHub
  call would have been skipped as a "duplicate" on retry.

Add an impact line only after a pilot, for example: "Reduced median time to acknowledge by
[X%] across [N] incidents while keeping every external write behind commander approval."
`scripts/measure_impact.py` prints its assumptions so the claim stays reproducible.

## Interview proof points

- **Why a graph?** The critic can send work back to research, the run pauses for a human,
  and both are checkpointed. A single prompt hides all three.
- **Why not let the model act?** Prompt injection and plain mistakes. The model can only
  propose from a closed list, code checks the proposal, and only n8n holds credentials.
- **What if the model is down or wrong?** The rules engine answers, the incident records
  why, and the guardrails apply to both. The UI shows which engine ran.
- **How do you know it works?** A labeled eval set in CI, plus tests for resume after
  restart, double approval, failed dispatch, and injection.
- **What breaks at scale?** SQLite and the in-process event bus mean one instance. The
  migration path (Postgres saver, shared pub/sub, Redis idempotency) is written down in
  `docs/DEPLOYMENT.md`.
- **What would you do next?** Per-user login, Slack signature verification, a Jira action,
  and publishing Claude's eval results.

## Portfolio checklist

- Record a 2-minute demo following [DEMO_SCRIPT.md](DEMO_SCRIPT.md).
- Add a screenshot or GIF of the incident page to the README.
- Link the architecture, the eval report, and the ADRs.
- If you run a pilot, record the inputs next to any number you quote.

## Honest metric language

Before a pilot:

> Scored on 10 labeled synthetic incidents (offline baseline: 10 of 10); every external
> write requires commander approval.

After a pilot:

> Reduced median time to acknowledge by [X%] across [N] incidents.

Keywords: Python, FastAPI, LangGraph, Claude API, structured output, guardrails, evals,
human-in-the-loop, idempotency, SQLite, React, TypeScript, Server-Sent Events, n8n,
Docker, GitHub Actions.
