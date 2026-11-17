# Evaluation suites

Every suite is **reproducible from the repo** (no manual steps). Reports are
committed or regenerated in CI so you can point reviewers at numbers, not demos.

## Incident workflow (`make eval`)

| Location | Count | What it checks |
|---|---:|---|
| [`dataset/*.json`](dataset/) | 10 | Core severity, grounding, status, conflicts |
| [`dataset/guardrails/*.json`](dataset/guardrails/) | 27 | Prompt injection, PII leak, metric junk, size limits |

Runner: [`run_evals.py`](run_evals.py) · Report: [`report.md`](report.md)

```bash
make eval                              # rules, all suites
make eval-claude                         # core 10, rules vs Claude if keyed
python -m evals.run_evals --suite core   # core only
```

## Runbook retrieval (`make eval-retrieval`)

| Location | Purpose |
|---|---|
| [`dataset/retrieval/*.json`](dataset/retrieval/) | Labeled query → expected `*.md` path |

Runner: [`run_retrieval_evals.py`](run_retrieval_evals.py) · Report: [`retrieval_report.md`](retrieval_report.md)

Modes:

- **fts** — raw query (baseline)
- **rewrite** — production path (deterministic expansion)
- **hybrid** — optional, needs `pip install -e '.[rag]'`

Metrics: recall@k, MRR, top-1 (see [`retrieval_scorers.py`](retrieval_scorers.py)).

Design narrative: [docs/RAG_SYSTEM_DESIGN.md](../docs/RAG_SYSTEM_DESIGN.md).

## Guardrail precision (`make eval-guardrails`)

Runner: [`guardrail_eval.py`](guardrail_eval.py) — attack block rate and benign false positives.

## Architecture benchmark (`make eval-architecture`)

Runner: [`compare_architectures.py`](compare_architectures.py) · Report: [`architecture_report.md`](architecture_report.md)

Compares 2-agent vs 5-agent specialist graph on the **same** cases and scorers.

## Adding a case

1. Add JSON under the right `dataset/` folder (copy an existing file).
2. Run the matching `make eval-*` target.
3. Commit the updated report if metrics are meant to be pinned (retrieval/incident).