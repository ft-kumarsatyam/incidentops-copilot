"""Backward-compatible re-exports. Use :mod:`incidentops.rag` for new code."""

from incidentops.rag.rewrite import expand_runbook_query, rewrite_metadata

__all__ = ["expand_runbook_query", "rewrite_metadata"]
