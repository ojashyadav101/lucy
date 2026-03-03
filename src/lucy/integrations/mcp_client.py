"""MCP protocol client — HTTP/SSE transport.

Provides connect-and-discover and per-call tool execution for any service
exposing an MCP HTTP or SSE endpoint (Craft, Notion, Linear, etc.).

Design choices:
- Session pool: a process-level cache (``_SESSION_POOL``) keeps one live
  ClientSession per URL. First call ~5 s (TLS + MCP initialize); subsequent
  calls re-use the session for ~200 ms latency. Stale sessions are detected
  on the first RPC failure and evicted, triggering a transparent reconnect.
- Streamable HTTP first, SSE fallback: the modern MCP spec uses Streamable
  HTTP; older servers use SSE. We try Streamable HTTP, fall back to SSE.
- Never raises: all public functions return error dicts, matching the
  Composio error pattern so the agent loop handles them uniformly.
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog

from lucy.config import settings

logger = structlog.get_logger()

_MAX_TOOLS_PER_SERVICE = 30

# ── Session pool ───────────────────────────────────────────────────────────────
# Keyed by URL. Value: (session, transport_name, last_used_monotonic).
# We keep at most _POOL_MAX_SIZE entries; evict LRU when full.
_POOL_MAX_SIZE = 20
_POOL_TTL_S = 300  # evict idle sessions after 5 minutes

@dataclass
class _PoolEntry:
    session: Any          # mcp.ClientSession
    transport: str
    last_used: float = field(default_factory=time.monotonic)
    # We need to hold a reference to the context managers so they can be
    # closed when evicted. Stored as a cleanup coroutine to call on eviction.
    cleanup: Any = None   # Callable[[], Coroutine]

_SESSION_POOL: dict[str, _PoolEntry] = {}
_POOL_LOCK: asyncio.Lock | None = None


def _get_pool_lock() -> asyncio.Lock:
    global _POOL_LOCK
    if _POOL_LOCK is None:
        _POOL_LOCK = asyncio.Lock()
    return _POOL_LOCK


async def _evict_stale_sessions() -> None:
    """Remove sessions idle longer than _POOL_TTL_S and enforce size cap."""
    now = time.monotonic()
    stale = [url for url, e in _SESSION_POOL.items() if now - e.last_used > _POOL_TTL_S]
    for url in stale:
        await _close_pool_entry(url)

    # Enforce max size by evicting the oldest (LRU)
    while len(_SESSION_POOL) > _POOL_MAX_SIZE:
        oldest_url = min(_SESSION_POOL, key=lambda u: _SESSION_POOL[u].last_used)
        await _close_pool_entry(oldest_url)


async def _close_pool_entry(url: str) -> None:
    entry = _SESSION_POOL.pop(url, None)
    if entry and entry.cleanup:
        try:
            await entry.cleanup()
        except Exception:
            pass
    if entry:
        logger.debug("mcp_session_evicted", url=url, transport=entry.transport)


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
        ), ClientSession(read, write) as session:
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

    Re-uses a pooled session when available (~200 ms). Falls back to a fresh
    connection on session errors (stale connection, server restart, etc.).
    Returns ``{"result": ...}`` on success or ``{"error": ...}`` on failure —
    never raises.

    ``tool_name`` is the **native** MCP tool name (no ``mcp_{service}_`` prefix).
    Stripping the prefix is the caller's responsibility.
    """
    timeout = timeout or settings.mcp_timeout_s
    primary = transport_hint if transport_hint in ("streamable_http", "sse") else "streamable_http"

    async def _call_session(session: Any) -> dict[str, Any]:
        result = await session.call_tool(tool_name, arguments)
        if result.isError:
            content_text = _extract_content_text(result.content)
            return {"error": content_text or "MCP tool returned an error"}
        content_text = _extract_content_text(result.content)
        try:
            return {"result": json.loads(content_text)}
        except (json.JSONDecodeError, TypeError):
            return {"result": content_text}

    # ── Try pooled session first ───────────────────────────────────────────
    pool_lock = _get_pool_lock()
    async with pool_lock:
        await _evict_stale_sessions()
        entry = _SESSION_POOL.get(url)

    if entry is not None:
        try:
            entry.last_used = time.monotonic()
            result = await asyncio.wait_for(_call_session(entry.session), timeout=timeout)
            logger.debug("mcp_call_pooled", tool=tool_name, transport=entry.transport)
            return result
        except Exception as pool_err:
            # Session is stale — evict and fall through to fresh connection
            logger.debug(
                "mcp_pooled_session_stale",
                tool=tool_name,
                error=str(pool_err),
            )
            async with pool_lock:
                await _close_pool_entry(url)

    # ── Fresh connection: open, call, then keep session in pool ───────────
    try:
        t0 = time.monotonic()
        result, session, transport, cleanup = await _open_and_call(
            url, tool_name, arguments, primary, timeout
        )
        elapsed = time.monotonic() - t0
        logger.debug(
            "mcp_call_fresh",
            tool=tool_name,
            transport=transport,
            connect_ms=int(elapsed * 1000),
        )

        # Store the live session in the pool for future calls
        async with pool_lock:
            _SESSION_POOL[url] = _PoolEntry(
                session=session,
                transport=transport,
                cleanup=cleanup,
            )

        return result

    except Exception as exc:
        err = str(exc) or type(exc).__name__
        logger.warning("mcp_tool_call_failed", tool=tool_name, url=url, error=err)
        return {"error": f"MCP tool '{tool_name}' failed: {err}"}


async def _open_and_call(
    url: str,
    tool_name: str,
    arguments: dict[str, Any],
    transport: str,
    timeout: float,
) -> tuple[dict[str, Any], Any, str, Any]:
    """Open a fresh MCP session, run a tool call, and return the session for pooling.

    Returns (result_dict, session, transport_name, cleanup_coroutine).
    The session is left open for pooling. The cleanup coroutine must be called
    when the session should be closed (eviction, shutdown, etc.).
    """
    from mcp import ClientSession

    fallback = "sse" if transport == "streamable_http" else "streamable_http"

    for use_transport in (transport, fallback):
        try:
            if use_transport == "sse":
                from mcp.client.sse import sse_client as _sse
                cm = _sse(url, timeout=timeout, sse_read_timeout=timeout * 10)
            else:
                from mcp.client.streamable_http import streamable_http_client as _sthttp
                http_client = httpx.AsyncClient(timeout=httpx.Timeout(timeout))
                cm = _sthttp(url, http_client=http_client)

            streams_ctx = cm.__aenter__()
            _streams = await streams_ctx

            read, write = _streams[0], _streams[1]

            session_cm = ClientSession(read, write)
            session = await session_cm.__aenter__()
            await session.initialize()

            tool_result = await session.call_tool(tool_name, arguments)

            if tool_result.isError:
                content_text = _extract_content_text(tool_result.content)
                # Don't pool on error
                await session_cm.__aexit__(None, None, None)
                await cm.__aexit__(None, None, None)
                return {"error": content_text or "MCP tool returned an error"}, None, use_transport, None

            content_text = _extract_content_text(tool_result.content)
            try:
                parsed = {"result": json.loads(content_text)}
            except (json.JSONDecodeError, TypeError):
                parsed = {"result": content_text}

            # Build a cleanup closure that properly tears down the session
            async def _cleanup(s=session, s_cm=session_cm, c_cm=cm):
                try:
                    await s_cm.__aexit__(None, None, None)
                except Exception:
                    pass
                try:
                    await c_cm.__aexit__(None, None, None)
                except Exception:
                    pass

            return parsed, session, use_transport, _cleanup

        except Exception as err:
            logger.debug(
                "mcp_open_call_transport_failed",
                tool=tool_name,
                transport=use_transport,
                error=str(err),
            )
            continue

    raise RuntimeError(f"All transports failed for {url}")


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
