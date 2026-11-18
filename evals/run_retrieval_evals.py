"""Benchmark runbook retrieval (FTS vs hybrid vs query rewrite, SQLite and Postgres).

python -m evals.run_retrieval_evals
python -m evals.run_retrieval_evals --modes fts,rewrite,hybrid
TEST_DATABASE_URL=postgresql://... python -m evals.run_retrieval_evals \\
    --modes fts,rewrite,pg-fts,pgvector

``pg-fts`` and ``pgvector`` run the shared Postgres store (``RUNBOOK_STORE=postgres``) in a
throwaway database on the server named by ``TEST_DATABASE_URL``; ``pgvector`` also needs the
pgvector extension and the ``rag`` extra.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evals.retrieval_scorers import effective_query, score_case
from incidentops.rag import (
    HybridRetriever,
    PostgresRetriever,
    index_runbooks,
    index_runbooks_postgres,
    index_runbooks_with_embeddings,
    search_runbooks,
)

ROOT = Path(__file__).resolve().parents[1]
DATASET = Path(__file__).parent / "dataset" / "retrieval"
RECALL_GATE = 0.10  # hybrid must beat FTS by this much on mean recall@k to change default


@dataclass
class CaseRow:
    name: str
    scores: dict[str, float | bool]
    top_path: str | None


@dataclass
class ModeReport:
    mode: str
    rows: list[CaseRow] = field(default_factory=list)

    @property
    def mean_recall(self) -> float:
        if not self.rows:
            return 0.0
        return sum(float(r.scores["recall_at_k"]) for r in self.rows) / len(self.rows)

    @property
    def mean_mrr(self) -> float:
        if not self.rows:
            return 0.0
        return sum(float(r.scores["mrr"]) for r in self.rows) / len(self.rows)

    @property
    def top1_rate(self) -> float:
        if not self.rows:
            return 0.0
        return sum(1.0 for r in self.rows if r.scores["top1"]) / len(self.rows)


def load_cases() -> list[dict[str, Any]]:
    return [json.loads(path.read_text("utf-8")) for path in sorted(DATASET.glob("*.json"))]


def _hybrid_available() -> bool:
    try:
        import fastembed  # noqa: F401
    except ImportError:
        return False
    return True


POSTGRES_MODES = ("pg-fts", "pgvector")


@contextmanager
def _scratch_database(admin_url: str) -> Iterator[str]:
    """A throwaway database on the server, so the eval never touches real data."""
    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    name = f"incidentops_eval_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield make_conninfo(**{**conninfo_to_dict(admin_url), "dbname": name})
    finally:
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _postgres_available(mode: str) -> str | None:
    """Why a Postgres mode cannot run here, or None when it can."""
    if not os.environ.get("TEST_DATABASE_URL"):
        return "TEST_DATABASE_URL is not set"
    try:
        import psycopg  # noqa: F401
    except ImportError:
        return "the postgres extra is not installed"
    if mode == "pgvector" and not _hybrid_available():
        return "fastembed is not installed (install the [rag] extra)"
    return None


def _build_index(mode: str, database: Path) -> None:
    if mode == "hybrid":
        index_runbooks_with_embeddings(ROOT / "runbooks", database)
    else:
        index_runbooks(ROOT / "runbooks", database)


def _run_postgres(mode: str, cases: list[dict[str, Any]]) -> ModeReport:
    report = ModeReport(mode)
    with _scratch_database(os.environ["TEST_DATABASE_URL"]) as url:
        hybrid = mode == "pgvector"
        index_runbooks_postgres(ROOT / "runbooks", url, embeddings=hybrid)
        retriever = PostgresRetriever(url, hybrid=hybrid)
        for case in cases:
            prefer = case.get("prefer")
            hits = retriever.search(
                effective_query(case), int(case.get("k", 3)), prefer=str(prefer) if prefer else None
            )
            report.rows.append(
                CaseRow(
                    name=case["name"],
                    scores=score_case(hits, case),
                    top_path=hits[0]["path"] if hits else None,
                )
            )
    return report


def run_mode(mode: str, cases: list[dict[str, Any]]) -> ModeReport:
    if mode in POSTGRES_MODES:
        return _run_postgres(mode, cases)
    report = ModeReport(mode)
    with tempfile.TemporaryDirectory() as tmp:
        database = Path(tmp) / "runbooks.db"
        _build_index("hybrid" if mode == "hybrid" else "fts", database)
        retriever = HybridRetriever(database) if mode == "hybrid" else None
        for case in cases:
            prefer = case.get("prefer")
            prefer_str = str(prefer) if prefer else None
            raw = str(case["query"])
            query = raw if mode == "fts" else effective_query(case)
            limit = int(case.get("k", 3))
            if retriever is not None:
                hits = retriever.search(query, limit, prefer=prefer_str)
            else:
                hits = search_runbooks(database, query, limit, prefer=prefer_str)
            scored = score_case(hits, case)
            report.rows.append(
                CaseRow(
                    name=case["name"],
                    scores=scored,
                    top_path=hits[0]["path"] if hits else None,
                )
            )
    return report


def render(reports: list[ModeReport]) -> str:
    lines = [
        "# Runbook retrieval evaluation",
        "",
        f"{len(reports[0].rows)} labeled queries against the repo runbooks.",
        "",
        "| Mode | Mean recall@k | Mean MRR | Top-1 rate |",
        "|---|---:|---:|---:|",
    ]
    for rep in reports:
        lines.append(
            f"| {rep.mode} | {rep.mean_recall:.2%} | {rep.mean_mrr:.3f} | {rep.top1_rate:.2%} |"
        )
    for rep in reports:
        lines += [
            "",
            f"## {rep.mode}",
            "",
            "| Case | recall@k | MRR | top-1 | first hit |",
            "|---|---:|---:|---|---|",
        ]
        for row in rep.rows:
            lines.append(
                f"| {row.name} | {float(row.scores['recall_at_k']):.0%} | "
                f"{float(row.scores['mrr']):.2f} | {'yes' if row.scores['top1'] else 'no'} | "
                f"{row.top_path or '-'} |"
            )
    fts = next((r for r in reports if r.mode == "fts"), None)
    hybrid = next((r for r in reports if r.mode == "hybrid"), None)
    if fts and hybrid:
        delta = hybrid.mean_recall - fts.mean_recall
        rewrite = next((r for r in reports if r.mode == "rewrite"), None)
        lines += [
            "",
            "## Adoption gate",
            "",
            (
                f"Hybrid recall@k minus raw FTS: **{delta:+.2%}** "
                f"(hybrid default gate: ≥{RECALL_GATE:.0%})."
            ),
        ]
        if rewrite:
            lines.append(
                f"Query rewrite mean recall@k: **{rewrite.mean_recall:.2%}** — "
                "production uses FTS + rewrite; hybrid optional "
                "([ADR 007](../docs/adr/007-hybrid-runbook-retrieval.md))."
            )
        lines.append("")
    pg_modes = [r.mode for r in reports if r.mode in POSTGRES_MODES]
    if pg_modes:
        lines += [
            "",
            "`pg-fts` and `pgvector` run the shared Postgres store "
            "(`RUNBOOK_STORE=postgres`) with the rewritten query, like `rewrite` and `hybrid` do "
            "on SQLite. `pgvector` also searches the HNSW vector index, so it can return a runbook "
            "that shares no words with the query.",
            "",
        ]
    lines += ["Regenerate with `make eval-retrieval`."]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--modes",
        default="fts,rewrite",
        help="comma-separated: fts, rewrite, hybrid, pg-fts, pgvector",
    )
    parser.add_argument("--out", type=Path, default=Path(__file__).parent / "retrieval_report.md")
    parser.add_argument(
        "--min-recall",
        type=float,
        default=1.0,
        help="fail if rewrite mode mean recall@k is below this",
    )
    args = parser.parse_args()
    modes = [part.strip() for part in args.modes.split(",") if part.strip()]
    if "hybrid" in modes and not _hybrid_available():
        print("fastembed not installed; skipping hybrid (install [rag] extra)", file=sys.stderr)
        modes = [m for m in modes if m != "hybrid"]
    for mode in [m for m in modes if m in POSTGRES_MODES]:
        reason = _postgres_available(mode)
        if reason:
            print(f"skipping {mode}: {reason}", file=sys.stderr)
            modes.remove(mode)
    cases = load_cases()
    reports = [run_mode(mode, cases) for mode in modes]
    text = render(reports)
    args.out.write_text(text, encoding="utf-8")
    print(text)
    rewrite = next((r for r in reports if r.mode == "rewrite"), None)
    if rewrite and rewrite.mean_recall < args.min_recall:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
