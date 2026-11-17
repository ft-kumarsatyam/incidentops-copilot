"""Optional external MCP client (requires ``pip install incidentops-copilot[mcp]``)."""

from typing import Any

from incidentops.core.config import Settings


def call_external_mcp(
    state: dict[str, Any],
    settings: Settings,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Placeholder for stdio MCP servers listed in settings.mcp_servers."""
    _ = (state, settings)
    return [], []