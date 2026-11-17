"""MCP-shaped read bridge; local stubs until external MCP processes are configured."""

from typing import Any

from incidentops.core.config import Settings
from incidentops.core.logging import get_logger

log = get_logger(__name__)


def append_mcp_evidence(
    state: dict[str, Any],
    settings: Settings,
) -> tuple[list[dict[str, Any]], list[str]]:
    if not settings.mcp_enabled:
        return [], []
    blocks: list[dict[str, Any]] = []
    lines: list[str] = []
    for server in settings.mcp_servers[: settings.rolegraph_max_parallel]:
        name = server.get("name", "local")
        blocks.append(
            {
                "source": f"mcp:{name}",
                "data": {
                    "status": "bridge",
                    "note": "MCP read path enabled; configure external servers in MCP_SERVERS",
                    "service": state.get("service", ""),
                },
            }
        )
        lines.append(f"mcp bridge attached for server {name}")
    if not settings.mcp_servers:
        blocks.append(
            {
                "source": "mcp:local",
                "data": {
                    "status": "bridge",
                    "note": "MCP_ENABLED without MCP_SERVERS uses in-process evidence only",
                },
            }
        )
        lines.append("mcp enabled (in-process bridge)")
    try:
        from incidentops.tools.mcp.client import call_external_mcp

        extra, extra_lines = call_external_mcp(state, settings)
        blocks.extend(extra)
        lines.extend(extra_lines)
    except ImportError:
        pass
    except Exception as error:
        log.warning("mcp.read_failed", error=type(error).__name__)
        lines.append(f"mcp read skipped ({type(error).__name__})")
    return blocks, lines