# Deployment

IncidentOps runs as one API process (which also serves the built UI) plus,
optionally, n8n. This page covers running it for real.

## Choose a mode

| Mode | Command | Use for |
|---|---|---|
| Local dev | `make dev` (+ `make web-dev`) | Working on the code |
| Docker Compose | `docker compose up --build` | A demo, or a small team pilot on one host |
| Your own host | `incidentops serve --host 0.0.0.0` | Running under systemd or another supervisor |

## Environments and fail-closed auth

`INCIDENTOPS_ENV=dev` lets the API start without a key. Any other value requires
`INCIDENTOPS_API_KEY`, and the process refuses to start without one. Generate it with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

People paste the key into **Settings** in the UI. It stays in that browser's session storage unless they tick "remember".

## Secrets

| Secret | Held by | Notes |
|---|---|---|
| `INCIDENTOPS_API_KEY` | API, n8n, the browser | Rotate with the others when staff change |
| `N8N_ACTION_TOKEN` | API and n8n | Must match on both sides |
| `ZAPIER_ACTION_WEBHOOK_URL` | API | Treat the Catch Hook URL as a credential |
| `ZAPIER_ACTION_TOKEN` | API and Zap | Optional additional shared token |
| `ANTHROPIC_API_KEY` | API | Optional. Without it the rules engine runs |
| `GITHUB_TOKEN` | n8n (issue write), API (read, optional) | Use a fine-grained token on one repository |
| `SLACK_BOT_TOKEN` | n8n | The API never sees it |

Keep them in your platform's secret store. `.env` is git-ignored; `.env.example`
holds placeholders only. Compose falls back to obvious `local-*` defaults, so
set real values before exposing anything.

## Docker Compose

```bash
cp .env.example .env     # set INCIDENTOPS_API_KEY, N8N_ACTION_TOKEN, N8N_ENCRYPTION_KEY
docker compose up --build
```

- The API listens on `:8000` and has a health check; n8n waits for it.
- Data lives in the `incidentops-data` volume (`/app/.data`). n8n data lives in `n8n-data`.
- The n8n image is pinned (`N8N_IMAGE`). Upgrade it deliberately.
- The container runs as a non-root user.
- Runbooks are indexed at startup (`AUTO_INDEX_RUNBOOKS=true`).

For Zapier instead of n8n, set `ACTION_PROVIDER=zapier` and
`ZAPIER_ACTION_WEBHOOK_URL`; see [`zapier/README.md`](../zapier/README.md).

Put a TLS-terminating reverse proxy in front of both services. For the live
stream, do not buffer responses on `/incidents/stream`.

## Health and readiness

- `GET /health`: the process is up. Use it for liveness.
- `GET /ready`: database reachable and runbook index present, with the selected
  `action_provider` and `action_webhook` status. Returns `503` if not ready. Use it
  for load balancer checks.

## Logging and tracing

Logs are JSON on stdout (`LOG_JSON=true`, the default; `make dev` turns it off for readability), each line
carrying `request_id` and, inside a run, `run_id`. Ship them with your usual agent.

For traces, install the extra and point at a collector:

```bash
pip install -e ".[otel]"
OTEL_EXPORTER_OTLP_ENDPOINT=http://collector:4318 incidentops serve
```

## Backups

All state is in `DATABASE_PATH` (default `.data/incidentops.db`), plus WAL files
beside it. Use SQLite's online backup so the copy is consistent:

```bash
sqlite3 .data/incidentops.db ".backup 'backup.db'"
```

The runbook index is derived from `runbooks/` and can be rebuilt with
`incidentops runbooks index`.

## Capacity and limits

- **One API instance.** SQLite plus the in-process event bus assume it.
- `RATE_LIMIT_PER_MINUTE` is per client IP plus API key, and per process. A reverse proxy is the real defense.
- `MAX_BODY_BYTES` caps request size (default 256 KB).
- Claude calls are bounded by `AI_TIMEOUT_SECONDS` and `AI_MAX_OUTPUT_TOKENS`, and
  the critic loop by `MAX_RESEARCH_ATTEMPTS`. Per-run cost therefore has a ceiling.

## Runbook index at scale

The default index is SQLite FTS5 plus optional embeddings in the same file
(`RUNBOOK_RETRIEVAL=hybrid`, `pip install '.[rag]'`). That fits tens of runbooks
on one host.

When the corpus grows to hundreds of documents or multiple API instances need a
shared index:

1. Keep the `RunbookRetriever` interface; swap the store to **Pgvector** or **Qdrant**.
2. Build embeddings in CI or a batch job; serve search from the shared store.
3. Leave query rewrite and grounding rules unchanged so triage citations stay valid.

See [ADR 007](adr/007-hybrid-runbook-retrieval.md).

## Scaling out

To run several API instances:

1. Switch the checkpointer to `PostgresSaver` and port `IncidentRepo` (two tables).
2. Replace `EventBus` with Redis pub/sub or Postgres `LISTEN/NOTIFY`. The rest of
   the code only calls `publish` and `subscribe`.
3. Move rate limiting to the proxy or Redis.
4. Replace n8n's static-data idempotency gate with Redis `SET NX` or a unique constraint.

## Upgrades

The schema is created on startup with `CREATE TABLE IF NOT EXISTS`. There are no
migrations yet. Before an upgrade, take a backup. When a release changes the schema,
the changelog will say so.
