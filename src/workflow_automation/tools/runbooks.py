"""Dependency-free runbook retrieval using SQLite FTS5."""

import re
import sqlite3
from pathlib import Path
from typing import Any

HEADING = re.compile(r"^#{1,3}\s+(.+)$", re.MULTILINE)
WORD = re.compile(r"[A-Za-z0-9_]{2,}")


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


def index_runbooks(directory: Path, database: Path) -> int:
    root = directory.resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    rows: list[tuple[str, str, str]] = []
    if root.is_dir():
        for path in sorted(root.rglob("*.md")):
            resolved = path.resolve()
            if not resolved.is_file() or not resolved.is_relative_to(root):
                continue
            text = resolved.read_text(encoding="utf-8")
            relative = str(resolved.relative_to(root))
            rows.extend((relative, title, content) for title, content in _chunks(text, path.stem))

    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS runbook_chunks "
            "USING fts5(path UNINDEXED, title, content)"
        )
        connection.execute("DELETE FROM runbook_chunks")
        connection.executemany(
            "INSERT INTO runbook_chunks(path, title, content) VALUES (?, ?, ?)",
            rows,
        )
    return len(rows)


def search_runbooks(database: Path, query: str, limit: int = 3) -> list[dict[str, Any]]:
    if not database.is_file() or limit < 1:
        return []
    terms = list(dict.fromkeys(word.lower() for word in WORD.findall(query)))[:12]
    if not terms:
        return []
    expression = " OR ".join(f'"{term}"' for term in terms)
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT path, title, snippet(runbook_chunks, 2, '[', ']', '…', 24) AS excerpt, "
            "bm25(runbook_chunks) AS score FROM runbook_chunks "
            "WHERE runbook_chunks MATCH ? ORDER BY score LIMIT ?",
            (expression, limit),
        ).fetchall()
    return [dict(row) for row in rows]

