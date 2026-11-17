# Contributing

This project favors **explicit, testable graph steps** over large prompts. When you
add behavior, extend the graph, the guardrails and the evals together.

## Setup

```bash
make setup        # virtualenv, dependencies, .env, web dependencies, pre-commit hooks
make dev          # API on :8000
make web-dev      # UI on :5173 with hot reload (second terminal)
```

Requirements: Python 3.11+, Node 22+ for the UI.

## Quality gates

```bash
make check        # lint, mypy, tests with coverage, evals, UI typecheck and tests
make web-test     # just the UI typecheck and tests
```

Individual targets: `make lint`, `make format`, `make typecheck`, `make test`,
`make cov` (fails under 85%), `make eval`. Run `make help` for the full list.
CI runs the same gates on Python 3.11 and 3.13, builds the UI, and builds the
Docker image.

## Where things live

| To change | Look in |
|---|---|
| An endpoint | `src/incidentops/api/routes/` |
| Workflow steps or routing | `src/incidentops/workflows/incident/` |
| What the AI may propose | `src/incidentops/agents/guardrails.py`, `prompts/` |
| Retrieval | `src/incidentops/tools/runbooks.py`, `runbooks/` |
| The UI | `web/src/` |
| The n8n flow | `n8n/incident-ops.json` and its contract test |

## Adding or changing AI behavior

1. Change the code or prompt.
2. Add a labeled case to `evals/dataset/` that fails without your change.
3. Run `make eval`. Keep the rules baseline passing; regenerate `evals/report.md`.
4. If a guardrail changes, add a unit test in `tests/agents/`.
5. Describe the change in `docs/AI_DESIGN.md` if the rules of the game moved.

`make eval-claude` spends real money and needs `ANTHROPIC_API_KEY`. Run it deliberately.

## Tests

Tests live under `tests/{unit,workflow,api,agents,contract}`. Prefer the fake provider
over network calls. A new workflow path needs one test for the golden trajectory and
one for a failure that ends in `needs_human` or `failed`.

## Decisions

Anything that changes the architecture gets a short ADR in `docs/adr/`.

## Pull requests

- Keep each PR to one concern, with tests.
- Say how you verified it, and include a screenshot for UI changes.
- Update `CHANGELOG.md` under **Unreleased**.
- Do not commit secrets. `.env` is ignored; keep `.env.example` to placeholders.
