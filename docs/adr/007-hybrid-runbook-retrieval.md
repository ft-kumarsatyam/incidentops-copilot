# ADR 007: Hybrid runbook retrieval (optional, eval-gated default)

**Status:** Accepted  
**Date:** 2026-11-18

## Context

Runbooks are retrieved with SQLite FTS5 (BM25) over heading chunks
([`src/incidentops/tools/runbooks.py`](../../src/incidentops/tools/runbooks.py)).
That is offline, reproducible, and sufficient for a small curated corpus.

Symptom language often does not match runbook headings (for example “users cannot
complete purchases”). Two mitigations were added without changing the triage graph:

1. **Query rewrite** — deterministic service aliases and symptom phrases
   ([`src/incidentops/rag/rewrite.py`](../../src/incidentops/rag/rewrite.py)).
2. **Hybrid retrieval** — FTS shortlist, then dense rerank with local embeddings
   stored in the same SQLite file ([`src/incidentops/rag/hybrid.py`](../../src/incidentops/rag/hybrid.py)),
   enabled with `RUNBOOK_RETRIEVAL=hybrid` and the `[rag]` optional extra (`fastembed`).

See also [RAG system design](../RAG_SYSTEM_DESIGN.md).

## Measurement

`evals/run_retrieval_evals.py` scores labeled queries with recall@k, MRR, and
top-1 path. Modes:

| Mode | Meaning |
|---|---|
| `fts` | Raw query, FTS only (baseline) |
| `rewrite` | Expanded query, FTS (production default path) |
| `hybrid` | Expanded query, FTS + dense rerank (optional install) |

**Adoption gate for changing the default** from `fts` to `hybrid`: hybrid must
improve mean recall@k by at least **10 percentage points** on the labeled suite
with no regression on the main incident evals (`make eval`).

Regenerate [`evals/retrieval_report.md`](../../evals/retrieval_report.md) with
`make eval-retrieval`.

## Decision

- **Default remains `RUNBOOK_RETRIEVAL=fts`** with query rewrite applied in the
  research node (no extra dependencies).
- **Hybrid is available** for pilots and portfolio demos when `pip install '.[rag]'`
  and runbooks are re-indexed.
- **Do not** require a separate vector database for the current corpus size;
  embeddings live in SQLite. For larger corpora, migrate the same retriever
  interface to Pgvector or Qdrant ([`docs/DEPLOYMENT.md`](../DEPLOYMENT.md)).

Re-evaluate the default when the retrieval suite grows or FTS recall on `rewrite`
mode falls below the CI gate.