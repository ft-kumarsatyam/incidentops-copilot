# Contributing

This project is optimized for **clear, reviewable agent graphs**. When adding behavior, prefer extending the graph and tools over growing monolithic prompts.

## Development setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

## Quality gates

```bash
ruff check src tests
ruff format src tests
mypy src
pytest
```

## Adding a new workflow

1. Add ADR or section in `docs/ARCHITECTURE.md` describing agents and edges.
2. Implement under `src/workflow_automation/workflows/<name>/`.
3. Register CLI in `workflow_automation.cli`.
4. Add tests: at least one **golden trajectory** and one **failure → escalate** case.
5. Update README demo table and resume bullets template in `docs/RESUME_AND_PORTFOLIO.md`.

## Pull requests

- Keep PRs scoped to one workflow or one cross-cutting concern (tools, escalation).
- Include a short "How to demo" note in the PR body.