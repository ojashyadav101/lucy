"""Composio client wrapper for Lucy.

Uses the modern Composio SDK session-based API:
  composio = Composio(provider=OpenAIProvider())
  session = composio.create(user_id=...)
  tools = session.tools()          # OpenAI-compatible tool schemas
  session.authorize("github")      # Generate auth link
  session.toolkits()               # Check connection status

The session returns "meta tools" (COMPOSIO_SEARCH_TOOLS, COMPOSIO_MULTI_EXECUTE_TOOL,
COMPOSIO_MANAGE_CONNECTIONS, etc.) that handle tool discovery and execution
through Composio's tool router. The LLM calls these meta-tools, and Composio
routes to the right underlying API (Google Calendar, Gmail, GitHub, etc.).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import structlog
from typing import Any, List, Dict

from composio import Composio
from composio_openai import OpenAIProvider

from lucy.config import settings

logger = structlog.get_logger()


class ComposioClient:
    """Modern Composio SDK wrapper using session-based API."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.composio_api_key
        self._tools_cache: dict[str, tuple[datetime, list[dict[str, Any]]]] = {}
        self._toolkits_cache: dict[str, tuple[datetime, list[dict[str, Any]]]] = {}
        self._cache_ttl = timedelta(minutes=5)
        try:
            self.composio = Composio(
                api_key=self.api_key,
                provider=OpenAIProvider(),
            )
            logger.info("composio_client_initialized")
        except Exception as e:
            logger.error("composio_client_init_failed", error=str(e))
            self.composio = None

    def _create_session(
        self,
        user_id: str,
        toolkits: list[str] | None = None,
    ):
        """Create a Composio session for a user.

        Args:
            user_id: Workspace/user identifier.
            toolkits: Optional list of toolkit slugs to restrict scope.

        Returns:
            ToolRouterSession object.
        """
        if not self.composio:
            raise RuntimeError("Composio client not initialized")
        kwargs: dict[str, Any] = {"user_id": user_id}
        if toolkits:
            kwargs["toolkits"] = toolkits
        return self.composio.create(**kwargs)

    async def _run_with_retry(self, fn: Any, operation: str, retries: int = 3) -> Any:
        """Run blocking Composio operations with exponential backoff retries."""
        last_error: Exception | None = None
        for attempt in range(retries):
            try:
                return await asyncio.to_thread(fn)
            except Exception as e:
                last_error = e
                is_last = attempt == retries - 1
                logger.warning(
                    "composio_retry",
                    operation=operation,
                    attempt=attempt + 1,
                    retries=retries,
                    error=str(e),
                    final=is_last,
                )
                if is_last:
                    break
                await asyncio.sleep(0.4 * (2**attempt))
        raise last_error if last_error else RuntimeError(f"{operation} failed")

    async def get_tools(
        self,
        user_id: str,
        apps: List[str] | None = None,
    ) -> List[Dict[str, Any]]:
        """Get OpenAI-compatible tool schemas via Composio session.

        Uses the modern session.tools() API which returns meta-tools
        (COMPOSIO_SEARCH_TOOLS, COMPOSIO_MULTI_EXECUTE_TOOL, etc.)
        that handle tool routing automatically.

        Args:
            user_id: Workspace/user identifier.
            apps: Optional list of toolkit slugs to restrict scope.

        Returns:
            List of OpenAI tool schemas.
        """
        if not self.composio:
            return []

        apps_key = ",".join(sorted(apps)) if apps else "*"
        cache_key = f"{user_id}:{apps_key}"
        now = datetime.now(timezone.utc)
        cached = self._tools_cache.get(cache_key)
        if cached and cached[0] > now:
            logger.debug("composio_tools_cache_hit", user_id=user_id, apps=apps)
            return cached[1]

        try:
            def _get():
                session = self._create_session(user_id, toolkits=apps)
                return session.tools()

            tools = await self._run_with_retry(_get, "get_tools")
            self._tools_cache[cache_key] = (now + self._cache_ttl, tools)
            logger.info(
                "composio_tools_fetched",
                count=len(tools),
                user_id=user_id,
                apps=apps,
            )
            return tools
        except Exception as e:
            logger.error("composio_get_tools_failed", error=str(e), apps=apps)
            return []

    async def get_connected_toolkits(
        self,
        user_id: str,
    ) -> list[dict[str, Any]]:
        """Check which toolkits a user has connected.

        Args:
            user_id: Workspace/user identifier.

        Returns:
            List of toolkit info dicts with name, slug, and connected status.
        """
        if not self.composio:
            return []

        now = datetime.now(timezone.utc)
        cached = self._toolkits_cache.get(user_id)
        if cached and cached[0] > now:
            logger.debug("composio_toolkits_cache_hit", user_id=user_id)
            return cached[1]

        try:
            def _get():
                session = self._create_session(user_id)
                toolkits = session.toolkits()
                items = getattr(toolkits, "items", [])
                result = []
                for tk in items:
                    conn = getattr(tk, "connection", None)
                    is_active = getattr(conn, "is_active", False) if conn else False
                    ca = getattr(conn, "connected_account", None) if conn else None
                    ca_id = getattr(ca, "id", None) if ca else None
                    result.append({
                        "name": getattr(tk, "name", "unknown"),
                        "slug": getattr(tk, "slug", "unknown"),
                        "connected": is_active,
                        "connected_account_id": ca_id,
                        "status": "ACTIVE" if is_active else "INACTIVE",
                    })
                return result

            result = await self._run_with_retry(_get, "get_connected_toolkits")
            self._toolkits_cache[user_id] = (now + self._cache_ttl, result)
            return result
        except Exception as e:
            logger.warning("composio_get_toolkits_failed", error=str(e))
            return []

    async def create_auth_link(
        self,
        user_id: str,
        toolkit: str,
    ) -> str | None:
        """Generate an OAuth connection link for a toolkit.

        Uses the modern session.authorize() API.

        Args:
            user_id: Workspace/user identifier.
            toolkit: Toolkit slug (e.g. 'googlecalendar', 'github').

        Returns:
            OAuth redirect URL or None.
        """
        if not self.composio:
            return None

        try:
            def _authorize():
                session = self._create_session(user_id)
                connection_request = session.authorize(toolkit)
                return getattr(connection_request, "redirect_url", None) or \
                       getattr(connection_request, "redirectUrl", None)

            url = await self._run_with_retry(_authorize, "create_auth_link")
            logger.info("composio_auth_link_created", toolkit=toolkit, user_id=user_id)
            return url
        except Exception as e:
            logger.error("composio_auth_link_failed", toolkit=toolkit, error=str(e))
            return None

    async def is_toolkit_connected(
        self,
        user_id: str,
        toolkit: str,
    ) -> bool:
        """Check if a specific toolkit is connected for a user.

        Args:
            user_id: Workspace/user identifier.
            toolkit: Toolkit slug.

        Returns:
            True if connected.
        """
        toolkits = await self.get_connected_toolkits(user_id)
        for tk in toolkits:
            if tk.get("slug", "").lower() == toolkit.lower() and tk.get("connected"):
                return True
        return False

    async def fetch_app_tool_schemas(
        self,
        user_id: str,
        apps: list[str],
    ) -> list[tuple[str, list[dict]]]:
        """Fetch per-tool schemas for specified apps, grouped by app slug.

        Returns a list of (app_slug, schemas) tuples so the caller knows
        which toolkit each batch belongs to (used by BM25 index).

        Args:
            user_id: Workspace identifier (used for per-entity auth context).
            apps: List of Composio app slugs (e.g. ["googlecalendar", "github"]).

        Returns:
            List of (app_slug, [schema, ...]) tuples.
        """
        if not self.composio or not apps:
            return []

        results: list[tuple[str, list[dict]]] = []

        for app in apps:
            cache_key = f"direct:{user_id}:{app}"
            now = datetime.now(timezone.utc)
            cached = self._tools_cache.get(cache_key)
            if cached and cached[0] > now:
                logger.debug("composio_direct_schemas_cache_hit", user_id=user_id, app=app)
                results.append((app, cached[1]))
                continue

            try:
                def _get(toolkit=app):
                    return self.composio.tools.get(user_id=user_id, toolkits=[toolkit], limit=100)

                schemas = await self._run_with_retry(_get, f"fetch_app_tool_schemas:{app}")
                normalised: list[dict] = []
                for s in (schemas or []):
                    if isinstance(s, dict):
                        normalised.append(s)
                    elif hasattr(s, "__dict__"):
                        normalised.append(vars(s))
                    else:
                        try:
                            import json as _j
                            normalised.append(_j.loads(str(s)))
                        except Exception:
                            pass

                self._tools_cache[cache_key] = (now + self._cache_ttl, normalised)
                logger.info("composio_direct_schemas_fetched", count=len(normalised), user_id=user_id, app=app)
                results.append((app, normalised))
            except Exception as e:
                logger.error("composio_fetch_schemas_failed", error=str(e), app=app)

        return results

    async def create_connection_link(self, entity_id: str, app: str) -> str | None:
        """Compatibility wrapper used by registry/handlers."""
        return await self.create_auth_link(user_id=entity_id, toolkit=app.lower())

    async def get_entity_connections(self, entity_id: str) -> list[dict[str, Any]]:
        """Compatibility wrapper returning normalized connection records."""
        toolkits = await self.get_connected_toolkits(entity_id)
        return [
            {
                "app": tk.get("slug", "unknown"),
                "status": "ACTIVE" if tk.get("connected") else "INACTIVE",
                "connected_account_id": tk.get("connected_account_id"),
            }
            for tk in toolkits
        ]

    async def execute_action(
        self,
        action: str,
        params: dict[str, Any],
        entity_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute a composio action/meta-tool with retries."""
        if not self.composio:
            return {"error": "Composio not initialized"}
        user_id = entity_id or "default"

        def _exec():
            return self.composio.tools.execute(
                slug=action,
                arguments=params or {},
                user_id=user_id,
                dangerously_skip_version_check=True,
            )

        try:
            result = await self._run_with_retry(_exec, f"execute_action:{action}")
            if isinstance(result, dict):
                return result
            return {"result": str(result)}
        except Exception as e:
            return {"error": str(e), "error_type": "execution_failed", "action": action}


_composio_client: ComposioClient | None = None


def get_composio_client() -> ComposioClient:
    """Get singleton Composio client."""
    global _composio_client
    if _composio_client is None:
        _composio_client = ComposioClient()
    return _composio_client
