"""Lexical runbook index and BM25 search (SQLite FTS5)."""

import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

HEADING = re.compile(r"^#{1,3}\s+(.+)$", re.MULTILINE)
WORD = re.compile(r"[A-Za-z0-9_]{2,}")


def _doc_title(text: str, stem: str) -> str:
    """Document-level text (H1 plus file name) so chunks match on the service they describe."""
    first = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    return f"{first.group(1).strip() if first else ''} {stem.replace('-', ' ')}".strip()


def _chunks(text: str, fallback_title: str) -> list[tuple[str, str]]:
    matches = list(HEADING.finditer(text))
    if not matches:
        return [(fallback_title, text[:4000])] if text.strip() else []
    chunks: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        stop = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        content = text[start:stop].strip()
        if content:
            chunks.append((match.group(1).strip(), content[:4000]))
    return chunks


def load_chunks(directory: Path) -> list[tuple[str, str, str, str]]:
    """Every runbook chunk as ``(path, doc, title, content)``. Shared by every index backend."""
    root = directory.resolve()
    rows: list[tuple[str, str, str, str]] = []
    if root.is_dir():
        for path in sorted(root.rglob("*.md")):
            resolved = path.resolve()
            if not resolved.is_file() or not resolved.is_relative_to(root):
                continue
            text = resolved.read_text(encoding="utf-8")
            relative = str(resolved.relative_to(root))
            doc = _doc_title(text, path.stem)
            rows.extend(
                (relative, doc, title, content) for title, content in _chunks(text, path.stem)
            )
    return rows


def index_runbooks(directory: Path, database: Path) -> int:
    database.parent.mkdir(parents=True, exist_ok=True)
    rows = load_chunks(directory)

    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("DROP TABLE IF EXISTS runbook_chunks")
        connection.execute(
            "CREATE VIRTUAL TABLE runbook_chunks USING fts5(path UNINDEXED, doc, title, content)"
        )
        connection.executemany(
            "INSERT INTO runbook_chunks(path, doc, title, content) VALUES (?, ?, ?, ?)",
            rows,
        )
    return len(rows)


def search_runbooks(
    database: Path, query: str, limit: int = 3, prefer: str | None = None
) -> list[dict[str, Any]]:
    """BM25 search. ``prefer`` is a service name: its same-named runbook ranks first."""
    if not database.is_file() or limit < 1:
        return []
    terms = list(dict.fromkeys(word.lower() for word in WORD.findall(query)))[:12]
    if not terms:
        return []
    expression = " OR ".join(f'"{term}"' for term in terms)
    name = f"{prefer.strip().lower()}.md" if prefer else ""
    try:
        with closing(sqlite3.connect(database)) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                "SELECT path, title, snippet(runbook_chunks, 3, '[', ']', '…', 24) AS excerpt, "
                "bm25(runbook_chunks, 1.0, 2.0, 2.0, 1.0) AS score FROM runbook_chunks "
                "WHERE runbook_chunks MATCH :query "
                "ORDER BY (:name != '' "
                "AND (lower(path) = :name OR lower(path) LIKE '%/' || :name)) DESC, "
                "score LIMIT :limit",
                {"query": expression, "name": name, "limit": limit},
            ).fetchall()
    except sqlite3.Error:
        return []
    return [dict(row) for row in rows]
