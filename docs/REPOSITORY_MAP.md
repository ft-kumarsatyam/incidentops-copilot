# Repository map

Where to look for each concern. Optimized for reviewers and future you.

## Top level

| Path | Purpose |
|---|---|
| [`src/incidentops/`](../src/incidentops/) | Python package (API, agents, workflow, RAG, persistence) |
| [`web/`](../web/) | React dashboard (Vite, TypeScript) |
| [`evals/`](../evals/) | Labeled quality gates (incidents, guardrails, retrieval, architecture) |
| [`runbooks/`](../runbooks/) | Markdown corpus for RAG |
| [`tests/`](../tests/) | Pytest (mirrors package layout) |
| [`docs/`](../docs/) | Architecture, ADRs, portfolio, **RAG system design** |
| [`n8n/`](../n8n/) | Slack/GitHub workflow import |
| [`scripts/`](../scripts/) | Ops helpers (e.g. impact estimator) |

## `src/incidentops/` (backend)

```text
api/           HTTP: FastAPI factory, routes, SSE, middleware, auth
agents/        LLM providers (Claude, rules), schemas, guardrails, prompts
orchestration/ RoleGraph: parallel roles, merge, TopologyRegistry
workflows/     LangGraph incident graph + IncidentService
rag/           Runbook RAG: FTS index, query rewrite, hybrid rerank, retriever API
tools/         External tools (GitHub, monitoring, n8n) + legacy RAG shims
persistence/   Incident read-model + audit log (SQLite)
core/          Settings, logging, redaction, telemetry, errors
cli.py         incidentops CLI (serve, demo, runbooks index/query)
```

**Rule of thumb:** agents **reason**, `rag/` **retrieves**, `tools/` **calls out**,
workflows **orchestrate**, API **exposes**.

## `evals/` (quality)

| Suite | Directory / runner | CI |
|---|---|---|
| Incidents (core + guardrails) | `dataset/`, `run_evals.py` | yes (`make eval`) |
| Retrieval | `dataset/retrieval/`, `run_retrieval_evals.py` | yes (`fts,rewrite`) |
| Architecture benchmark | `compare_architectures.py` | manual |
| Guardrail metrics | `guardrail_eval.py` | yes |

See [evals/README.md](../evals/README.md).

## `tests/`

```text
unit/          Fast tests (rag, runbooks, config, …)
workflow/      LangGraph + AI behavior
api/           HTTP contracts
agents/        Provider and specialist experiments
```

## `docs/` (reading order for interviews)

1. [AI_SYSTEM_DESIGN.md](AI_SYSTEM_DESIGN.md) — canonical AI layers and flows
2. [PORTFOLIO.md](PORTFOLIO.md) — 3-minute story
3. [RAG_SYSTEM_DESIGN.md](RAG_SYSTEM_DESIGN.md) — deep dive on retrieval
4. [AI_DESIGN.md](AI_DESIGN.md) — agents + guardrails (detail)
5. [ARCHITECTURE.md](ARCHITECTURE.md) — runtime and state
   ([END_TO_END_ARCHITECTURE.md](END_TO_END_ARCHITECTURE.md) — full picture, RBAC, roadmap)
6. [adr/](adr/) — decisions with evidence

## Commands cheat sheet

```bash
make check            # full CI locally
make eval-retrieval   # RAG metrics → evals/retrieval_report.md
make index            # rebuild runbook DB
incidentops runbooks query "checkout 5xx"
```