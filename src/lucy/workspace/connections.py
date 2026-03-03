"""MCP connection persistence for a workspace.

Each workspace stores its active MCP connections in ``data/mcp_connections.json``
as a JSON list of MCPConnectionRecord objects.

The file is read on every agent startup (pure filesystem, zero network calls)
and written when the user connects, disconnects, or refreshes an MCP server.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

from lucy.workspace.filesystem import WorkspaceFS

logger = structlog.get_logger()

_CONNECTIONS_FILE = "data/mcp_connections.json"


@dataclass
class MCPConnectionRecord:
    """Persistent record of a connected MCP server."""

    service: str
    mcp_url: str
    transport: str = "streamable_http"
    tools_cache: list[dict[str, Any]] = field(default_factory=list)
    tool_count: int = 0
    installed_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        now = datetime.now(UTC).isoformat()
        if not self.installed_at:
            self.installed_at = now
        if not self.updated_at:
            self.updated_at = now


async def load_mcp_connections(ws: WorkspaceFS) -> list[MCPConnectionRecord]:
    """Load all MCP connections for this workspace.

    Returns an empty list if no connections file exists yet.
    """
    raw = await ws.read_file(_CONNECTIONS_FILE)
    if not raw:
        return []
    try:
        data: list[dict[str, Any]] = json.loads(raw)
        records: list[MCPConnectionRecord] = []
        for item in data:
            records.append(
                MCPConnectionRecord(
                    service=item.get("service", ""),
                    mcp_url=item.get("mcp_url", ""),
                    transport=item.get("transport", "streamable_http"),
                    tools_cache=item.get("tools_cache", []),
                    tool_count=item.get("tool_count", 0),
                    installed_at=item.get("installed_at", ""),
                    updated_at=item.get("updated_at", ""),
                )
            )
        return records
    except (json.JSONDecodeError, KeyError) as exc:
        logger.warning(
            "mcp_connections_load_failed",
            workspace_id=ws.workspace_id,
            error=str(exc),
        )
        return []


async def save_mcp_connection(ws: WorkspaceFS, record: MCPConnectionRecord) -> None:
    """Upsert an MCP connection record.

    If a record with the same service slug already exists it is replaced;
    otherwise the new record is appended. Written atomically.
    """
    existing = await load_mcp_connections(ws)
    updated: list[MCPConnectionRecord] = [
        r for r in existing if r.service != record.service
    ]
    record.updated_at = datetime.now(UTC).isoformat()
    updated.append(record)

    await ws.write_file(
        _CONNECTIONS_FILE,
        json.dumps([asdict(r) for r in updated], indent=2),
    )
    logger.info(
        "mcp_connection_saved",
        workspace_id=ws.workspace_id,
        service=record.service,
        tool_count=record.tool_count,
    )


async def delete_mcp_connection(ws: WorkspaceFS, service: str) -> bool:
    """Remove an MCP connection by service slug.

    Returns True if a record was found and removed, False if not found.
    """
    existing = await load_mcp_connections(ws)
    filtered = [r for r in existing if r.service != service]
    if len(filtered) == len(existing):
        return False

    await ws.write_file(
        _CONNECTIONS_FILE,
        json.dumps([asdict(r) for r in filtered], indent=2),
    )
    logger.info(
        "mcp_connection_deleted",
        workspace_id=ws.workspace_id,
        service=service,
    )
    return True


async def get_mcp_connection(
    ws: WorkspaceFS, service: str
) -> MCPConnectionRecord | None:
    """Retrieve a single MCP connection record by service slug, or None."""
    connections = await load_mcp_connections(ws)
    for conn in connections:
        if conn.service == service:
            return conn
    return None
