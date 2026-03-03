"""Token storage and OAuth connection protocol.

Defines the interface that any OAuth/token backend must implement.
Currently implemented by ComposioClient. Future implementations could
use iDrive, a self-hosted database, or any other token store.

To swap out Composio for iDrive (or any other backend):
1. Implement the OAuthBackend protocol below.
2. Update get_oauth_backend() to return your new implementation.
3. No other code needs to change — agent, resolver, and tool dispatch
   all call get_oauth_backend() rather than get_composio_client() directly.

Migration path:
  Current:  get_oauth_backend() → ComposioBackend → ComposioClient
  Future:   get_oauth_backend() → iDriveBackend   → iDriveClient
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class OAuthBackend(Protocol):
    """Protocol for an OAuth token + integration backend.

    Any class that satisfies this interface can replace Composio.
    """

    async def authorize(
        self,
        workspace_id: str,
        toolkit: str,
    ) -> dict[str, str | None]:
        """Generate an OAuth connection URL for a service.

        Returns:
            {"url": "https://...", "error": None}       on success
            {"url": None, "error": "human reason"}      on failure
        """
        ...

    async def get_connected_apps(
        self,
        workspace_id: str,
    ) -> list[dict[str, Any]]:
        """Return all active OAuth connections for a workspace.

        Each item is a dict with at least:
            - "appName": str   (service slug, e.g. "gmail")
            - "status":  str   (e.g. "ACTIVE")
        """
        ...

    async def get_connected_app_names_reliable(
        self,
        workspace_id: str,
    ) -> list[str]:
        """Return a flat list of connected service slugs for a workspace.

        E.g. ["gmail", "googlecalendar", "github"]
        """
        ...

    async def execute_tool_call(
        self,
        workspace_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute a tool call via the backend.

        Returns {"result": ...} on success or {"error": "..."} on failure.
        """
        ...

    def get_tools(
        self,
        workspace_id: str,
        *,
        include_sandbox: bool = True,
    ) -> list[dict[str, Any]]:
        """Return OpenAI-compatible tool definitions for the LLM.

        include_sandbox: whether to include bash/workbench execution tools.
        When False, only OAuth-backed tools (Gmail, Calendar, etc.) are returned.
        """
        ...


class ComposioBackend:
    """Thin wrapper that satisfies OAuthBackend using ComposioClient.

    Delegates all calls to the existing ComposioClient. This exists so
    the rest of the codebase can depend on OAuthBackend without importing
    composio_client directly, making a future migration trivial.
    """

    def __init__(self) -> None:
        from lucy.integrations.composio_client import get_composio_client
        self._client = get_composio_client()

    async def authorize(
        self,
        workspace_id: str,
        toolkit: str,
    ) -> dict[str, str | None]:
        return await self._client.authorize(workspace_id, toolkit)

    async def get_connected_apps(
        self,
        workspace_id: str,
    ) -> list[dict[str, Any]]:
        return await self._client.get_connected_apps(workspace_id)

    async def get_connected_app_names_reliable(
        self,
        workspace_id: str,
    ) -> list[str]:
        return await self._client.get_connected_app_names_reliable(workspace_id)

    async def execute_tool_call(
        self,
        workspace_id: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._client.execute_tool_call(workspace_id, tool_name, arguments)

    def get_tools(
        self,
        workspace_id: str,
        *,
        include_sandbox: bool = True,
    ) -> list[dict[str, Any]]:
        tools = self._client.get_tools(workspace_id)
        if not include_sandbox:
            sandbox_names = {"COMPOSIO_REMOTE_BASH_TOOL", "COMPOSIO_REMOTE_WORKBENCH"}
            tools = [t for t in tools if t.get("function", {}).get("name") not in sandbox_names]
        return tools


_backend: OAuthBackend | None = None


def get_oauth_backend() -> OAuthBackend:
    """Return the active OAuth backend.

    Currently returns ComposioBackend. To switch to iDrive (or any other
    provider), replace the line below:

        return ComposioBackend()
        # → return iDriveBackend()
    """
    global _backend
    if _backend is None:
        _backend = ComposioBackend()
    return _backend


def set_oauth_backend(backend: OAuthBackend) -> None:
    """Override the active OAuth backend (for testing or migration)."""
    global _backend
    _backend = backend
