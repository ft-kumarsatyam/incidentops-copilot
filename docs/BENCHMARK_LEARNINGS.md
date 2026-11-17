# Benchmark learnings: Ruflo vs DeepTutor → this repo

Sources (narrow slices; full repos are 4.5k–8.7k files):

| Repo | What it is | Size signal |
|------|------------|-------------|
| [ruvnet/ruflo](https://github.com/ruvnet/ruflo) | **Agent meta-harness** for Claude Code/Codex: swarms, MCP, memory, plugins | TypeScript platform; `npx ruflo init` drops agents/hooks into your project |
| [HKUDS/DeepTutor](https://github.com/HKUDS/DeepTutor) | **Lifelong tutoring product**: RAG, UI, partners, MCP, desktop | Python + web; agent-native runtime with tools/capabilities |

**Do not compete on their turf.** Compete on **one observable ops outcome** with **explicit graph + HITL** in ~500 lines of Python.

---

## What each repo optimizes for

```text
Ruflo:     User → Harness (MCP/router) → Swarm → Agents → Memory → LLM
DeepTutor: User → Chat UI → Agent loop (tools/capabilities) → RAG/KB → LLM
This repo: Trigger → LangGraph → Role nodes → Tools (GitHub/Slack) → HITL → Publish
```

| Dimension | Ruflo | DeepTutor | **This repo (target)** |
|-----------|-------|-----------|-------------------------|
| Primary buyer story | “Give Claude a nervous system” | “Personal tutor + knowledge” | “Cut weekly ops toil (status, triage)” |
| Orchestration | MCP workflows + JS fan-out | Single agent loop + plugins | **LangGraph** state machine |
| Human gates | `workflow_pause` / approval | `ask_user`, capability guards | **Interrupt before publish** |
| Memory / RAG | RuVector, federation | LightRAG, PageIndex, many engines | **Optional later** — YAGNI until v2 |
| Demo surface | Console TUI, plugins | Full web app | **CLI + Slack draft + 90s video** |
| Resume keyword hit | harness, swarm, MCP | multi-agent, RAG, agentic | **LangGraph, HITL, quantified ops** |

---

## Steal (minimal)

### From Ruflo [`ruflo-workflows`](https://github.com/ruvnet/ruflo/tree/main/plugins/ruflo-workflows)

1. **Named lifecycle** — `created → running → paused → completed | cancelled` maps 1:1 to our run status + HITL.
2. **Approval gates** — pause is a feature, not failure; resume carries same `run_id`.
3. **Two surfaces** — long-lived persisted workflow (us) vs one-shot `workflow_execute` (our `--dry-run`).

```text
# ponytail: mirror ruflo lifecycle in EngStatusState.status only — no MCP workflow_* layer
running → needs_human → running → succeeded
```

### From DeepTutor [`packages/chat-engine`](https://github.com/HKUDS/DeepTutor/tree/main/packages/chat-engine)

1. **Pause / resume in the protocol** — tool round can pause; unresolved pause must not burn another LLM round (same as our critic → escalate, not infinite retry).
2. **Capability guard** — critic is a guard: redirect once, then reject (maps to `retries < 2` then HITL).
3. **Product lesson** — they win on **readiness diagnostics** (`deeptutor doctor`). We add **`workflow-run eng-status --check`** (env + API smoke) before claiming “production-minded.”

---

## Skip (YAGNI for portfolio)

| Ruflo | DeepTutor |
|-------|-----------|
| 98 agents, swarm federation, ruOS | Full Next.js UI, 15 IM channels |
| 406 MCP tools | GraphRAG / LightRAG stack |
| Plugin marketplace, Rust vector engine | Books, mastery paths, desktop releases |
| Claude Code plugin-only path | Partner ecosystem, 200k LOC runtime |

Trying to “match” either repo on GitHub stars **loses** against instant-apply portfolios. Interviewers want **one traced run**, not a platform skeleton.

---

## Best “GenAI project” shape for *your* repo

**Positioning:** *Ops Workflow Harness* — a **narrow LangGraph reference implementation** of patterns both repos prove at scale:

| # | Deliverable | Borrowed from |
|---|-------------|---------------|
| 1 | One LangGraph with **critic loop + cap** | DeepTutor run-loop guards |
| 2 | **Status enum + pause/resume** on same `run_id` | Ruflo workflow lifecycle |
| 3 | **GitHub → Slack** with draft channel | DeepTutor “publish only when ready” |
| 4 | **`--check` + structured logs** | DeepTutor doctor / Ruflo console cost |
| 5 | **Optional MCP read-only** exposing `run_status` only | Ruflo surface area without 406 tools |

**Secondary module (invoice triage)** stays optional — DeepTutor-style RAG only if you add `data/fixtures/` and a field-confidence score, not a KB product.

---

## Resume differentiation vs these repos

| If interviewer says… | You answer… |
|----------------------|-------------|
| “Why not use Ruflo?” | “Ruflo is a **host harness** for Claude Code; I implemented a **bounded Python graph** for a **specific ops SLA** with LangSmith and Slack HITL.” |
| “Why not DeepTutor?” | “DeepTutor is a **vertical learning product**; I focused on **engineering ops automation** with explicit state and measurable time saved.” |
| “Where’s multi-agent?” | “Planner/researcher/writer/critic are **roles in one graph**, not 98 agents — easier to test and demo in 90 seconds.” |

Suggested title line: **LangGraph Ops Harness** (sub: patterns from Ruflo workflows + DeepTutor agent loop).

---

## Implementation delta (update Walkthrough order)

Insert after Phase 2:

| Task | Rationale |
|------|-----------|
| `RunStatus` enum matching ruflo lifecycle | Interview whiteboard |
| `workflow-run --check` | DeepTutor doctor equivalent |
| Max 2 critic→researcher retries then `needs_human` | chat-engine pause semantics |
| Persist `runs/{run_id}.json` checkpoint | ruflo `workflow_resume` story |

Skip until v2: MCP server, vector memory, web UI, swarm.

---

## Graphify note

Full-repo graphify on ruflo/DeepTutor exceeds 5M words. Benchmark code was inspected via:

- `plugins/ruflo-workflows` (~42 files)
- `plugins/ruflo-swarm` (~49 files)
- `packages/chat-engine` (~34 files)

Cross-repo merged graph for **this project** lives in `graphify-out/graph.json` after doc refresh.