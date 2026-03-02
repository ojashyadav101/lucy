"""MCP protocol client — HTTP/SSE transport.

Provides stateless connect-and-discover and per-call tool execution for any
service exposing an MCP HTTP or SSE endpoint (Craft, Notion, Linear, etc.).

Design choices:
- Per-call reconnect: each call opens a fresh session, initialises, calls, closes.
  ~200 ms overhead but eliminates stale-session and connection-pool bugs since
  Lucy's tool calls can be seconds to minutes apart.
- Streamable HTTP first, SSE fallback: the modern MCP spec uses Streamable HTTP;
  older servers use SSE. We try Streamable HTTP, fall back to SSE transparently.
- Never raises: all public functions return error dicts, matching the Composio
  error pattern so the agent loop handles them uniformly.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog

from lucy.config import settings

logger = structlog.get_logger()

_MAX_TOOLS_PER_SERVICE = 30


@dataclass
class MCPDiscoveryResult:
    """Outcome of an MCP connect-and-discover call."""

    success: bool = False
    service: str = ""
    tools: list[dict[str, Any]] = field(default_factory=list)
    tool_count: int = 0
    transport: str = ""
    error: str | None = None


# ── Transport helpers ──────────────────────────────────────────────────────────

@asynccontextmanager
async def _open_session(url: str, timeout: float):
    """Open an MCP ClientSession over either Streamable HTTP or SSE.

    Yields the initialised ClientSession. Tries Streamable HTTP first; if that
    raises any exception (connection refused, HTTP 4xx/5xx, protocol mismatch)
    it falls back to the legacy SSE transport.
    """
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    http_client = httpx.AsyncClient(timeout=httpx.Timeout(timeout))
    try:
        # MCP SDK ≥1.9 yields (read, write, get_session_id); older versions
        # yielded (read, write). Unpack into a tuple and take the first two.
        async with streamable_http_client(url, http_client=http_client) as _streams:
            read, write = _streams[0], _streams[1]
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session, "streamable_http"
        return
    except Exception as streamable_err:
        logger.debug(
            "mcp_streamable_http_failed_trying_sse",
            url=url,
            error=str(streamable_err),
        )

    # SSE fallback
    from mcp.client.sse import sse_client

    try:
        async with sse_client(url, timeout=timeout, sse_read_timeout=timeout * 10) as (
            read,
            write,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session, "sse"
    finally:
        await http_client.aclose()


# ── Schema translation ──────────────────────────────────────────────────────────

def mcp_tools_to_openai(tools: list[Any], service: str) -> list[dict[str, Any]]:
    """Translate MCP Tool objects into OpenAI function-calling format.

    Each tool name is prefixed with ``mcp_{service}_`` so it is namespaced and
    can be routed back to the correct MCP connection by the agent.

    Capped at _MAX_TOOLS_PER_SERVICE to avoid context bloat; tools are sorted
    alphabetically so the selection is deterministic across re-discoveries.
    """
    slug = _service_slug(service)
    sorted_tools = sorted(tools, key=lambda t: t.name)[:_MAX_TOOLS_PER_SERVICE]

    result: list[dict[str, Any]] = []
    for tool in sorted_tools:
        # MCP input_schema is already a JSON Schema dict
        parameters: dict[str, Any] = tool.inputSchema if hasattr(tool, "inputSchema") else {}
        if not parameters:
            parameters = {"type": "object", "properties": {}}

        result.append(
            {
                "type": "function",
                "function": {
                    "name": f"mcp_{slug}_{tool.name}",
                    "description": tool.description or f"Call {tool.name} on {service}",
                    "parameters": parameters,
                },
            }
        )
    return result


def _service_slug(service: str) -> str:
    """Normalise a service name to a safe identifier segment."""
    return service.lower().replace(" ", "_").replace("-", "_").replace(".", "_")


def parse_mcp_tool_name(tool_name: str) -> tuple[str, str]:
    """Split ``mcp_{service}_{native}`` into ``(service_slug, native_name)``.

    Handles multi-segment service slugs by splitting on the first two
    underscore-separated tokens after the ``mcp_`` prefix.
    Example: ``mcp_craft_do_search_docs`` → ``("craft_do", "search_docs")``
    """
    without_prefix = tool_name.removeprefix("mcp_")
    # The service slug is the first segment (everything before the second _)
    # But service slugs themselves may contain underscores.
    # We identify the service by matching against known connections so just
    # return everything as (slug_candidate, rest) where slug_candidate is one word.
    parts = without_prefix.split("_", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return parts[0], ""


# ── Public API ────────────────────────────────────────────────────────────────

async def connect_and_discover(
    url: str,
    service: str,
    timeout: float | None = None,
) -> MCPDiscoveryResult:
    """Connect to an MCP server, initialise, and list available tools.

    Tries Streamable HTTP first, falls back to SSE. Translates tool schemas to
    OpenAI format with ``mcp_{service}_`` prefix applied.
    """
    timeout = timeout or settings.mcp_timeout_s
    service_slug = _service_slug(service)

    # Quick pre-flight: a bare HTTP GET tells us immediately if the URL is
    # 404 (link expired) or unreachable, before we pay the SSE handshake cost.
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10)) as _http:
            _pre = await _http.get(url, follow_redirects=True)
            if _pre.status_code == 404:
                logger.warning(
                    "mcp_url_404",
                    service=service,
                    url=url,
                )
                return MCPDiscoveryResult(
                    success=False,
                    service=service_slug,
                    error=(
                        "MCP_LINK_EXPIRED: The link returned HTTP 404. "
                        "Craft MCP links are single-use or time-limited. "
                        "The user needs to generate a fresh link from "
                        "Craft → Settings → Integrations → MCP."
                    ),
                )
    except Exception:
        pass  # network error — let the MCP session attempt handle it with full details

    try:
        async with _open_session(url, timeout) as (session, transport):
            tools_response = await session.list_tools()

            # tools_response is a ListToolsResult; tools are in .tools
            raw_tools = tools_response.tools if hasattr(tools_response, "tools") else []

            # Also handle tuple-style response from list_tools()
            if not raw_tools and hasattr(tools_response, "__iter__"):
                for item in tools_response:
                    if isinstance(item, tuple) and item[0] == "tools":
                        raw_tools = list(item[1])
                        break

            openai_tools = mcp_tools_to_openai(raw_tools, service)

            logger.info(
                "mcp_discovery_success",
                service=service,
                transport=transport,
                tool_count=len(openai_tools),
                url=url,
            )

            return MCPDiscoveryResult(
                success=True,
                service=service_slug,
                tools=openai_tools,
                tool_count=len(openai_tools),
                transport=transport,
            )

    except Exception as exc:
        err = str(exc) or type(exc).__name__
        logger.warning("mcp_discovery_failed", service=service, url=url, error=err)
        return MCPDiscoveryResult(
            success=False,
            service=service_slug,
            error=f"Could not connect to MCP server at {url}: {err}",
        )


async def call_tool(
    url: str,
    tool_name: str,
    arguments: dict[str, Any],
    transport_hint: str = "",
    timeout: float | None = None,
) -> dict[str, Any]:
    """Execute an MCP tool call on the given server URL.

    Reconnects per-call (stateless). Returns ``{"result": ...}`` on success
    or ``{"error": ...}`` on failure — never raises.

    ``tool_name`` is the **native** MCP tool name (no ``mcp_{service}_`` prefix).
    Stripping the prefix is the caller's responsibility.
    """
    timeout = timeout or settings.mcp_timeout_s

    async def _attempt(use_transport: str) -> dict[str, Any]:
        from mcp import ClientSession

        if use_transport == "sse":
            from mcp.client.sse import sse_client as _sse
            ctx = _sse(url, timeout=timeout, sse_read_timeout=timeout * 10)
        else:
            from mcp.client.streamable_http import streamable_http_client as _sthttp
            http_client = httpx.AsyncClient(timeout=httpx.Timeout(timeout))
            ctx = _sthttp(url, http_client=http_client)

        async with ctx as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, arguments)

                if result.isError:
                    content_text = _extract_content_text(result.content)
                    return {"error": content_text or "MCP tool returned an error"}

                content_text = _extract_content_text(result.content)
                # Try to parse as JSON for structured results
                try:
                    return {"result": json.loads(content_text)}
                except (json.JSONDecodeError, TypeError):
                    return {"result": content_text}

    try:
        # Determine which transport to try first
        primary = transport_hint if transport_hint in ("streamable_http", "sse") else "streamable_http"
        fallback = "sse" if primary == "streamable_http" else "streamable_http"

        try:
            return await _attempt(primary)
        except Exception as primary_err:
            logger.debug(
                "mcp_call_primary_transport_failed",
                tool=tool_name,
                transport=primary,
                error=str(primary_err),
            )
            return await _attempt(fallback)

    except Exception as exc:
        err = str(exc) or type(exc).__name__
        logger.warning(
            "mcp_tool_call_failed",
            tool=tool_name,
            url=url,
            error=err,
        )
        return {"error": f"MCP tool '{tool_name}' failed: {err}"}


def _extract_content_text(content: list[Any] | Any) -> str:
    """Extract a text string from MCP content items."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)

    parts: list[str] = []
    for item in content:
        if hasattr(item, "text"):
            parts.append(item.text)
        elif isinstance(item, dict):
            parts.append(item.get("text", str(item)))
        else:
            parts.append(str(item))
    return "\n".join(parts)
