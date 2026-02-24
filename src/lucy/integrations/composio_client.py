"""Composio integration client using the session-based meta-tools API.

Sessions provide 5 meta-tools that let the LLM discover and execute
any of Composio's 10,000+ tools at runtime:

    COMPOSIO_SEARCH_TOOLS        — find tools by use-case
    COMPOSIO_MANAGE_CONNECTIONS  — check/create OAuth connections
    COMPOSIO_MULTI_EXECUTE_TOOL  — execute up to 20 tools in parallel
    COMPOSIO_REMOTE_WORKBENCH    — run Python in a sandbox
    COMPOSIO_REMOTE_BASH_TOOL    — run bash in a sandbox

Execution flows through the session object so meta-tools share context
(e.g. SEARCH_TOOLS stores results that MULTI_EXECUTE_TOOL uses next).
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from lucy.config import settings

logger = structlog.get_logger()

_RETRYABLE_KEYWORDS = frozenset({
    "500", "502", "503", "504",
    "402", "601", "901",
    "timeout", "temporarily", "rate limit", "connection reset",
})


def _is_retryable(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(kw in msg for kw in _RETRYABLE_KEYWORDS)


class _RetryableComposioError(Exception):
    pass


class ComposioClient:
    """Composio SDK wrapper using session-based meta-tools.

    All tool execution routes through the session so meta-tool context
    (search results, connection state) is preserved across calls.
    """

    _MAX_CACHED_SESSIONS = 200

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.composio_api_key
        self._composio: Any = None
        self._session_cache: dict[str, tuple[datetime, Any]] = {}
        self._session_id_cache: dict[str, str] = {}
        self._tools_cache: dict[str, tuple[datetime, list[dict[str, Any]]]] = {}
        self._cache_ttl = timedelta(minutes=30)
        self._toolkit_versions: dict[str, str] = {}
        self._cache_lock = asyncio.Lock()
        self._session_lock = threading.Lock()
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
        """Get or create a Composio session for a workspace.

        Thread-safe with double-checked locking. Includes LRU eviction
        (max _MAX_CACHED_SESSIONS) and stale session auto-recovery.
        """
        if not self._composio:
            raise RuntimeError("Composio SDK not initialized")

        now = datetime.now(timezone.utc)

        cached = self._session_cache.get(workspace_id)
        if cached and cached[0] > now:
            return cached[1]

        with self._session_lock:
            cached = self._session_cache.get(workspace_id)
            if cached and cached[0] > now:
                return cached[1]

            if len(self._session_cache) >= self._MAX_CACHED_SESSIONS:
                oldest_key = min(
                    self._session_cache,
                    key=lambda k: self._session_cache[k][0],
                )
                self._session_cache.pop(oldest_key, None)
                self._session_id_cache.pop(oldest_key, None)
                self._tools_cache.pop(oldest_key, None)
                logger.debug("composio_lru_eviction", evicted=oldest_key)

            session = self._composio.create(user_id=workspace_id)
            self._session_cache[workspace_id] = (now + self._cache_ttl, session)

            session_id = getattr(session, "id", None) or getattr(session, "session_id", None)
            if session_id:
                self._session_id_cache[workspace_id] = str(session_id)

        logger.debug("composio_session_created", workspace_id=workspace_id)
        return session

    def _get_session_with_recovery(self, workspace_id: str) -> Any:
        """Get session with auto-recovery on stale/expired sessions."""
        try:
            return self._get_session(workspace_id)
        except Exception as e:
            logger.warning(
                "composio_session_stale_recovering",
                workspace_id=workspace_id,
                error=str(e),
            )
            with self._session_lock:
                self._session_cache.pop(workspace_id, None)
                self._session_id_cache.pop(workspace_id, None)
            return self._get_session(workspace_id)

    async def get_tools(self, workspace_id: str) -> list[dict[str, Any]]:
        """Get the 5 meta-tool schemas for a workspace.

        Returns OpenAI-compatible tool definitions that can be passed
        directly to the LLM via OpenClaw.
        """
        if not self._composio:
            return []

        now = datetime.now(timezone.utc)

        async with self._cache_lock:
            cached = self._tools_cache.get(workspace_id)
            if cached and cached[0] > now:
                return cached[1]

        try:
            def _fetch() -> list:
                session = self._get_session_with_recovery(workspace_id)
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

            async with self._cache_lock:
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
        """Execute a meta-tool call through the session.

        Routes through the session object so meta-tools share context
        (SEARCH_TOOLS results are visible to MULTI_EXECUTE_TOOL, etc.).
        """
        if not self._composio:
            return {"error": "Composio not initialized"}

        # Auto-correct common toolkit name mistakes made by LLM or users
        reverse_toolkit_map = {}
        if tool_name == "COMPOSIO_MANAGE_CONNECTIONS" and "toolkits" in arguments:
            _TOOLKIT_NAME_MAP = {
                "google_drive": "googledrive",
                "google drive": "googledrive",
                "google_calendar": "googlecalendar",
                "google calendar": "googlecalendar",
                "google_sheets": "googlesheets",
                "google sheets": "googlesheets",
                "bright_data": "brightdata",
                "bright data": "brightdata",
                "gmail": "gmail",
                "github": "github",
                "linear": "linear",
                "vercel": "vercel",
                "google_search_console": "google_search_console",
                "google search console": "google_search_console"
            }
            original_toolkits = arguments.get("toolkits", [])
            if isinstance(original_toolkits, list):
                corrected_toolkits = []
                for tk in original_toolkits:
                    normalized_tk = str(tk).lower().strip()
                    corrected = _TOOLKIT_NAME_MAP.get(normalized_tk, normalized_tk)
                    corrected_toolkits.append(corrected)
                    reverse_toolkit_map[corrected] = str(tk)
                arguments["toolkits"] = corrected_toolkits

        @retry(
            retry=retry_if_exception_type(_RetryableComposioError),
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=1, max=10),
            reraise=True,
        )
        async def _execute_with_retry() -> dict[str, Any]:
            try:
                def _exec() -> Any:
                    session = self._get_session_with_recovery(workspace_id)
                    if hasattr(session, "handle_tool_call"):
                        return session.handle_tool_call(
                            tool_name=tool_name, arguments=arguments
                        )
                    if hasattr(session, "execute"):
                        return session.execute(tool_name, arguments)
                    try:
                        return self._composio.tools.execute(
                            slug=tool_name,
                            arguments=arguments,
                            user_id=workspace_id,
                            dangerously_skip_version_check=True,
                        )
                    except TypeError:
                        return self._composio.tools.execute(
                            slug=tool_name,
                            arguments=arguments,
                            user_id=workspace_id,
                        )

                result = await asyncio.to_thread(_exec)

                if isinstance(result, dict):
                    if result.get("error") and _is_retryable(
                        Exception(str(result["error"]))
                    ):
                        raise _RetryableComposioError(str(result["error"]))
                    return result
                if hasattr(result, "model_dump"):
                    return result.model_dump()
                return {"result": str(result)}

            except _RetryableComposioError:
                raise
            except Exception as e:
                if _is_retryable(e):
                    raise _RetryableComposioError(str(e)) from e
                raise

        try:
            result = await _execute_with_retry()
            logger.info(
                "composio_tool_executed",
                tool=tool_name,
                workspace_id=workspace_id,
            )
            
            # --- Architectural Auto-Repair for MANAGE_CONNECTIONS ---
            if tool_name == "COMPOSIO_MANAGE_CONNECTIONS" and isinstance(result, dict):
                data = result.get("data", {})
                if isinstance(data, dict) and "results" in data:
                    results_map = data.get("results", {})
                    failed_toolkits = [
                        k for k, v in results_map.items() 
                        if v.get("status") == "failed" and "not found" in str(v.get("error_message", "")).lower()
                    ]
                    
                    if failed_toolkits:
                        recovered_slugs = []
                        for ft in failed_toolkits:
                            try:
                                # Search for the proper toolkit name
                                search_res = await asyncio.to_thread(
                                    self._composio.tools.execute,
                                    slug="COMPOSIO_SEARCH_TOOLS",
                                    arguments={"query": ft.replace("_", " ")},
                                    user_id=workspace_id,
                                    dangerously_skip_version_check=True
                                )
                                search_data = getattr(search_res, "model_dump", lambda: search_res)() if hasattr(search_res, "model_dump") else search_res
                                if isinstance(search_data, dict):
                                    search_data = search_data.get("data", search_data)
                                results_list = search_data.get("results", []) if isinstance(search_data, dict) else search_data
                                
                                if isinstance(results_list, list) and len(results_list) > 0:
                                    first_result = results_list[0]
                                    possible_toolkits = first_result.get("toolkits", [])
                                    if possible_toolkits:
                                        # Only accept it if it's not a generic fallback like composio_search
                                        suggested_slug = possible_toolkits[0]
                                        if suggested_slug != "composio_search":
                                            original_request = reverse_toolkit_map.get(ft, ft)
                                            clean_original = original_request.lower().replace("_", "").replace(" ", "")
                                            clean_suggested = suggested_slug.lower().replace("_", "").replace(" ", "")
                                            
                                            # STRICT MATCHING: Only auto-repair if they are practically identical.
                                            # Prevents fuzzy search from mapping "Clerk" to "Moonclerk"
                                            if clean_original == clean_suggested:
                                                recovered_slugs.append(suggested_slug)
                                                # We need to map the recovered slug back to whatever the LLM originally asked for
                                                reverse_toolkit_map[suggested_slug] = original_request
                            except Exception as e:
                                logger.warning("composio_auto_repair_search_failed", toolkit=ft, error=str(e))
                        
                        if recovered_slugs:
                            try:
                                retry_res = await asyncio.to_thread(
                                    self._composio.tools.execute,
                                    slug="COMPOSIO_MANAGE_CONNECTIONS",
                                    arguments={"toolkits": recovered_slugs},
                                    user_id=workspace_id,
                                    dangerously_skip_version_check=True
                                )
                                retry_data = getattr(retry_res, "model_dump", lambda: retry_res)() if hasattr(retry_res, "model_dump") else retry_res
                                if isinstance(retry_data, dict):
                                    # Merge successful recoveries back into the original result
                                    retry_results_map = retry_data.get("data", {}).get("results", {}) if isinstance(retry_data.get("data"), dict) else retry_data.get("results", {})
                                    if retry_results_map:
                                        for k, v in retry_results_map.items():
                                            if v.get("status") != "failed":
                                                result["data"]["results"][k] = v
                                        # Remove the originally failed ones that we successfully replaced
                                        for ft in failed_toolkits:
                                            if isinstance(result.get("data"), dict) and isinstance(result["data"].get("results"), dict):
                                                if ft in result["data"]["results"]:
                                                    del result["data"]["results"][ft]
                            except Exception as e:
                                logger.warning("composio_auto_repair_retry_failed", error=str(e))
            
            # Map keys back to what the LLM originally requested
            if tool_name == "COMPOSIO_MANAGE_CONNECTIONS" and isinstance(result, dict):
                data = result.get("data", {})
                if isinstance(data, dict) and "results" in data:
                    results_map = data.get("results", {})
                    mapped_results = {}
                    for k, v in results_map.items():
                        original_k = reverse_toolkit_map.get(k, k)
                        # Also fix the inner toolkit name so LLM sees exact match
                        if isinstance(v, dict):
                            if "toolkit" in v:
                                v["toolkit"] = original_k
                            # Strip out verbose current_user_info to avoid truncating LLM context
                            if "current_user_info" in v:
                                del v["current_user_info"]
                        mapped_results[original_k] = v
                    result["data"]["results"] = mapped_results
            
            # Collect permanently failed toolkits (after all auto-repair)
            if tool_name == "COMPOSIO_MANAGE_CONNECTIONS" and isinstance(result, dict):
                data = result.get("data", {})
                if isinstance(data, dict) and "results" in data:
                    permanently_failed = [
                        k for k, v in data["results"].items()
                        if isinstance(v, dict)
                        and v.get("status") == "failed"
                        and "not found" in str(v.get("error_message", "")).lower()
                    ]
                    if permanently_failed:
                        result["_unresolved_services"] = permanently_failed
                        logger.info(
                            "composio_unresolved_services",
                            services=permanently_failed,
                            workspace_id=workspace_id,
                        )
            # --------------------------------------------------------

            return result

        except _RetryableComposioError as e:
            logger.error(
                "composio_execute_retries_exhausted",
                tool=tool_name,
                workspace_id=workspace_id,
                error=str(e),
            )
            return {"error": f"Tool execution failed after retries: {e}", "tool": tool_name}

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
            def _auth() -> str | None:
                session = self._get_session_with_recovery(workspace_id)
                request = session.authorize(toolkit)
                return (
                    getattr(request, "redirect_url", None)
                    or getattr(request, "redirectUrl", None)
                    or getattr(request, "url", None)
                )

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
            def _fetch() -> list[dict[str, Any]]:
                session = self._get_session_with_recovery(workspace_id)
                toolkits = session.toolkits()
                items = getattr(toolkits, "items", [])
                result: list[dict[str, Any]] = []
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

    async def get_connected_app_names(self, workspace_id: str) -> list[str]:
        """Return just the names of actively connected apps."""
        apps = await self.get_connected_apps(workspace_id)
        return [a["name"] for a in apps if a.get("connected")]

    async def get_connected_app_names_reliable(self, workspace_id: str) -> list[str]:
        """Get connected app names using multiple detection methods.

        Falls back to session.toolkits(is_connected=True) when the
        basic check returns suspiciously few results.
        """
        names = await self.get_connected_app_names(workspace_id)

        if len(names) < 3:
            try:
                def _fetch_connected() -> list[str]:
                    session = self._get_session_with_recovery(workspace_id)
                    toolkits = session.toolkits(is_connected=True)
                    items = getattr(toolkits, "items", [])
                    found: list[str] = []
                    for tk in items:
                        name = getattr(tk, "name", None) or getattr(tk, "slug", None)
                        if name and name not in found:
                            found.append(name)
                    return found

                fallback_names = await asyncio.to_thread(_fetch_connected)
                for n in fallback_names:
                    if n not in names:
                        names.append(n)
            except Exception as e:
                logger.warning(
                    "connected_toolkits_fallback_failed", error=str(e),
                )

        return names

    def invalidate_cache(self, workspace_id: str | None = None) -> None:
        """Clear cached sessions and tools for a workspace (or all)."""
        if workspace_id:
            self._session_cache.pop(workspace_id, None)
            self._session_id_cache.pop(workspace_id, None)
            self._tools_cache.pop(workspace_id, None)
        else:
            self._session_cache.clear()
            self._session_id_cache.clear()
            self._tools_cache.clear()


_client: ComposioClient | None = None


def get_composio_client() -> ComposioClient:
    """Get or create the singleton Composio client."""
    global _client
    if _client is None:
        _client = ComposioClient()
    return _client
