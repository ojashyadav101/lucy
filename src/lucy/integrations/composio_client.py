"""Composio integration client using the session-based meta-tools API.

Sessions provide 5 meta-tools that let the LLM discover and execute
any of Composio's 10,000+ tools at runtime:

    COMPOSIO_SEARCH_TOOLS        — find tools by use-case
    COMPOSIO_MANAGE_CONNECTIONS  — check/create OAuth connections
    COMPOSIO_MULTI_EXECUTE_TOOL  — execute up to 20 tools in parallel
    COMPOSIO_REMOTE_WORKBENCH    — run Python in a sandbox
    COMPOSIO_REMOTE_BASH_TOOL    — run bash in a sandbox
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

from lucy.config import settings

logger = structlog.get_logger()


class ComposioClient:
    """Composio SDK wrapper using session-based meta-tools."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.composio_api_key
        self._composio: Any = None
        self._session_cache: dict[str, tuple[datetime, Any]] = {}
        self._tools_cache: dict[str, tuple[datetime, list[dict[str, Any]]]] = {}
        self._cache_ttl = timedelta(minutes=10)
        self._init_sdk()

    def _init_sdk(self) -> None:
        try:
            from composio import Composio
            self._composio = Composio(api_key=self.api_key)
            logger.info("composio_client_initialized")
        except Exception as e:
            logger.error("composio_init_failed", error=str(e))
            self._composio = None

    def _get_session(self, workspace_id: str) -> Any:
        """Get or create a Composio session for a workspace."""
        if not self._composio:
            raise RuntimeError("Composio SDK not initialized")

        now = datetime.now(timezone.utc)
        cached = self._session_cache.get(workspace_id)
        if cached and cached[0] > now:
            return cached[1]

        session = self._composio.create(user_id=workspace_id)
        self._session_cache[workspace_id] = (now + self._cache_ttl, session)
        logger.debug("composio_session_created", workspace_id=workspace_id)
        return session

    async def get_tools(self, workspace_id: str) -> list[dict[str, Any]]:
        """Get the 5 meta-tool schemas for a workspace.

        Returns OpenAI-compatible tool definitions that can be passed
        directly to the LLM.
        """
        if not self._composio:
            return []

        now = datetime.now(timezone.utc)
        cached = self._tools_cache.get(workspace_id)
        if cached and cached[0] > now:
            return cached[1]

        try:
            def _fetch():
                session = self._get_session(workspace_id)
                return session.tools()

            tools = await asyncio.to_thread(_fetch)

            tool_list: list[dict[str, Any]] = []
            for t in tools:
                if isinstance(t, dict):
                    tool_list.append(t)
                elif hasattr(t, "model_dump"):
                    tool_list.append(t.model_dump())
                elif hasattr(t, "__dict__"):
                    tool_list.append(vars(t))

            self._tools_cache[workspace_id] = (now + self._cache_ttl, tool_list)
            logger.info(
                "composio_meta_tools_fetched",
                workspace_id=workspace_id,
                count=len(tool_list),
                names=[t.get("function", {}).get("name", "?") for t in tool_list],
            )
            return tool_list

        except Exception as e:
            logger.error("composio_get_tools_failed", error=str(e))
            return []

    async def execute_tool_call(
        self,
        workspace_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute a single meta-tool call and return the result.

        This is called when the LLM returns a tool_call for one of the
        5 Composio meta-tools.
        """
        if not self._composio:
            return {"error": "Composio not initialized"}

        try:
            def _exec():
                return self._composio.tools.execute(
                    slug=tool_name,
                    arguments=arguments,
                    user_id=workspace_id,
                )

            result = await asyncio.to_thread(_exec)

            if isinstance(result, dict):
                return result
            if hasattr(result, "model_dump"):
                return result.model_dump()
            return {"result": str(result)}

        except Exception as e:
            logger.error(
                "composio_execute_failed",
                tool=tool_name,
                workspace_id=workspace_id,
                error=str(e),
            )
            return {"error": str(e), "tool": tool_name}

    async def authorize(
        self,
        workspace_id: str,
        toolkit: str,
    ) -> str | None:
        """Generate an OAuth connection link for a toolkit.

        Returns the redirect URL for the user to complete auth.
        """
        if not self._composio:
            return None

        try:
            def _auth():
                session = self._get_session(workspace_id)
                request = session.authorize(toolkit)
                return getattr(request, "redirect_url", None) or \
                       getattr(request, "redirectUrl", None)

            url = await asyncio.to_thread(_auth)
            logger.info(
                "composio_auth_link_created",
                workspace_id=workspace_id,
                toolkit=toolkit,
            )
            return url

        except Exception as e:
            logger.error(
                "composio_authorize_failed",
                toolkit=toolkit,
                error=str(e),
            )
            return None

    async def get_connected_apps(self, workspace_id: str) -> list[dict[str, Any]]:
        """List connected apps/toolkits for a workspace."""
        if not self._composio:
            return []

        try:
            def _fetch():
                session = self._get_session(workspace_id)
                toolkits = session.toolkits()
                items = getattr(toolkits, "items", [])
                result = []
                for tk in items:
                    conn = getattr(tk, "connection", None)
                    is_active = getattr(conn, "is_active", False) if conn else False
                    result.append({
                        "name": getattr(tk, "name", "unknown"),
                        "slug": getattr(tk, "slug", "unknown"),
                        "connected": is_active,
                    })
                return result

            return await asyncio.to_thread(_fetch)

        except Exception as e:
            logger.warning("composio_get_apps_failed", error=str(e))
            return []

    def invalidate_cache(self, workspace_id: str | None = None) -> None:
        """Clear cached sessions and tools for a workspace (or all)."""
        if workspace_id:
            self._session_cache.pop(workspace_id, None)
            self._tools_cache.pop(workspace_id, None)
        else:
            self._session_cache.clear()
            self._tools_cache.clear()


_client: ComposioClient | None = None


def get_composio_client() -> ComposioClient:
    """Get or create the singleton Composio client."""
    global _client
    if _client is None:
        _client = ComposioClient()
    return _client
