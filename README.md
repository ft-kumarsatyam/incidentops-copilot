# IncidentOps Copilot

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![LangGraph](https://img.shields.io/badge/Orchestration-LangGraph-1C3C3C)](https://langchain-ai.github.io/langgraph/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A Slack-native incident coordinator built with Python, LangGraph, and n8n. It
turns an incomplete incident report into evidence, severity, ownership, and
proposed actions—then requires human approval before anything changes.

```text
Slack → n8n → intake → research → assess → critic ↺ → approval → action/timeline
```

This is a focused, testable workflow rather than an open-ended agent swarm. It
demonstrates bounded autonomy, evidence-backed decisions, retry limits,
idempotency, durable checkpoints, and human escalation.

## What it does

- Accepts incident reports from a Slack thread through n8n.
- Normalizes supplied GitHub and monitoring evidence.
- Assigns a deterministic severity and proposes reversible next actions.
- Re-runs research when required evidence is missing.
- Escalates ambiguous incidents to `needs_human` after two attempts.
- Pauses at `awaiting_approval` before n8n performs an external write.
- Preserves a stable `run_id`, timeline, and resumable checkpoint.

## Quick start

### Requirements

- Python 3.11 or newer
- `pip`
- n8n and Slack only for production integration testing

### Install

```bash
git clone https://github.com/ft-kumarsatyam/incidentops-copilot.git
cd incidentops-copilot
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
cp .env.example .env
```

No API key is required for the deterministic local demo.

### Run the demo

```bash
workflow-run check
workflow-run incident --fixture tests/fixtures/incident_complete.json
workflow-run incident --fixture tests/fixtures/incident_ambiguous.json
```

The complete fixture ends at `awaiting_approval`. The ambiguous fixture retries
research twice and ends at `needs_human` instead of inventing missing facts.

To exercise an explicit decision:

```bash
workflow-run incident \
  --fixture tests/fixtures/incident_complete.json \
  --approval approved
```

## Architecture

| Layer | Responsibility |
| --- | --- |
| Slack + n8n | Ingress, credentials, approval UI, and outbound actions |
| Python + LangGraph | State, research loop, assessment, critique, and checkpoints |
| GitHub + monitoring | Read-only investigation evidence |
| Incident commander | Approves, rejects, or edits proposed actions |

The Python graph never writes directly to Slack, Jira, or GitHub. It returns a
proposal to n8n, which validates the approval and uses the incident `run_id` as
an idempotency key before executing an action.

## Configuration

Copy `.env.example` to `.env` and set only the integrations you need:

| Variable | Purpose | Required |
| --- | --- | --- |
| `GITHUB_TOKEN` | Read-only incident investigation | No |
| `N8N_ACTION_WEBHOOK_URL` | Approved action callback | No |
| `CHECKPOINT_DIR` | Local checkpoint location | No |
| `MAX_RESEARCH_ATTEMPTS` | Bounded research retries | No |
| `CONFIDENCE_THRESHOLD` | Minimum automatic confidence | No |
| `LANGSMITH_API_KEY` | Optional LangSmith tracing | No |
| `OPENAI_API_KEY` | Reserved for a future LLM assessor | No |

Never commit `.env`; it is excluded by `.gitignore`.

## n8n and Slack integration

1. Import [`n8n/incident-ops.json`](n8n/incident-ops.json) into n8n.
2. Configure the environment values documented in [`n8n/README.md`](n8n/README.md).
3. Expose the Python workflow behind an authenticated service endpoint.
4. Point `INCIDENTOPS_URL` at that endpoint.
5. Test intake, approval, rejection, and duplicate delivery.

The checked-in n8n workflow is a portable template. Credential IDs and the
final Jira or GitHub action are intentionally selected in the target n8n
instance.

## Development

Run all quality gates before opening a pull request:

```bash
ruff check src tests scripts
ruff format --check src tests scripts
mypy src
pytest
```

Project layout:

```text
src/workflow_automation/       Python package and CLI
  tools/                       Monitoring and n8n boundaries
  workflows/incident/          State, graph, and node implementations
tests/                         Graph, contract, and metric tests
tests/fixtures/                Deterministic incident payloads
n8n/incident-ops.json          Importable orchestration workflow
docs/                          Architecture, contracts, and demo guides
scripts/measure_impact.py      Reproducible impact calculator
```

## Measuring impact

```bash
python scripts/measure_impact.py \
  --manual-minutes 20 \
  --automated-minutes 4 \
  --incidents 25
```

The script prints its assumptions with the result. Replace synthetic inputs
with pilot data before making an impact claim. Useful measures include median
time to acknowledge, coordination steps removed, and the percentage of
ambiguous incidents correctly escalated.

## Documentation

- [Architecture and graph lifecycle](docs/ARCHITECTURE.md)
- [Step-by-step walkthrough](docs/WALKTHROUGH.md)
- [API contract](docs/API_CONTRACT.md)
- [Demo script](docs/DEMO_SCRIPT.md)
- [Benchmark learnings](docs/BENCHMARK_LEARNINGS.md)
- [Resume and portfolio guide](docs/RESUME_AND_PORTFOLIO.md)
- [Contributing guide](CONTRIBUTING.md)

## Status and scope

The fixture-driven graph and integration contract are suitable for local demos
and extension. Before production use, add an authenticated HTTP boundary,
durable checkpoint storage, Slack signature verification, production-grade
idempotency storage, observability, and service-specific authorization.

## License

Released under the [MIT License](LICENSE).