"""Backward-compatible re-exports. Use :mod:`incidentops.rag` for new code."""

from incidentops.rag.fts import index_runbooks, search_runbooks

__all__ = ["index_runbooks", "search_runbooks"]
