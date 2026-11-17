# Retrieval eval cases

Each JSON file:

```json
{
  "name": "unique-case-id",
  "query": "natural language as if from an incident report",
  "prefer": "checkout-api",
  "expect_paths": ["checkout-api.md"],
  "k": 3
}
```

- **query** — tested raw in `fts` mode; expanded with `prefer` as service in `rewrite` / `hybrid`.
- **expect_paths** — at least one must appear in the top-`k` hits (recall@k).
- Add cases when production misses a runbook or after fixing indexing/rewrite bugs.

See [docs/RAG_SYSTEM_DESIGN.md](../../../docs/RAG_SYSTEM_DESIGN.md).