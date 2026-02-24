"""OpenClaw Gateway HTTP client.

Wraps the Tools Invoke API (POST /tools/invoke) so Lucy can
remotely execute commands, manage background processes, and
fetch web content on the VPS without SSH.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from lucy.config import settings

logger = structlog.get_logger()

_INVOKE_PATH = "/tools/invoke"
_DEFAULT_TIMEOUT = 120


class OpenClawGatewayClient:
    """Async client for the OpenClaw Tools Invoke HTTP API."""

    def __init__(self) -> None:
        base_url = settings.openclaw_base_url.rstrip("/")
        token = settings.openclaw_api_key
        if not base_url or not token:
            raise RuntimeError(
                "OpenClaw Gateway not configured. "
                "Set openclaw_base_url and openclaw_api_key in config."
            )

        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(connect=10.0, read=180.0, write=10.0, pool=30.0),
        )
        self._base_url = base_url
        logger.info("openclaw_gateway_client_initialized", base_url=base_url)

    async def close(self) -> None:
        await self._client.aclose()

    async def _invoke(self, tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        """Low-level invoke: POST /tools/invoke with tool + args."""
        payload: dict[str, Any] = {"tool": tool}
        if args:
            payload["args"] = args

        try:
            resp = await self._client.post(_INVOKE_PATH, json=payload)
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                error = data.get("error", {})
                raise OpenClawGatewayError(
                    f"Tool invoke failed: {error.get('message', str(error))}",
                    tool=tool,
                )
            return data.get("result", {})
        except httpx.HTTPStatusError as e:
            logger.error(
                "openclaw_invoke_http_error",
                tool=tool,
                status=e.response.status_code,
                body=e.response.text[:500],
            )
            raise OpenClawGatewayError(
                f"HTTP {e.response.status_code} from Gateway",
                tool=tool,
            ) from e
        except httpx.TimeoutException as e:
            logger.error("openclaw_invoke_timeout", tool=tool, error=str(e))
            raise OpenClawGatewayError(f"Timeout invoking {tool}", tool=tool) from e

    # ── exec tool ────────────────────────────────────────────

    async def exec_command(
        self,
        command: str,
        timeout: int = _DEFAULT_TIMEOUT,
        workdir: str | None = None,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Run a foreground shell command on the VPS."""
        args: dict[str, Any] = {"command": command, "timeout": timeout}
        if workdir:
            args["workdir"] = workdir
        if env:
            args["env"] = env
        return await self._invoke("exec", args)

    async def start_background(
        self,
        command: str,
        workdir: str | None = None,
        env: dict[str, str] | None = None,
    ) -> str:
        """Start a command in background mode. Returns the sessionId."""
        args: dict[str, Any] = {"command": command, "background": True}
        if workdir:
            args["workdir"] = workdir
        if env:
            args["env"] = env
        result = await self._invoke("exec", args)
        session_id = result.get("sessionId", "")
        if not session_id:
            raise OpenClawGatewayError(
                "No sessionId returned from background exec", tool="exec"
            )
        logger.info("openclaw_background_started", session_id=session_id, command=command[:80])
        return session_id

    # ── process tool ─────────────────────────────────────────

    async def poll_process(self, session_id: str) -> dict[str, Any]:
        """Poll a background session for new output and exit status."""
        return await self._invoke("process", {"action": "poll", "sessionId": session_id})

    async def log_process(
        self, session_id: str, limit: int = 200, offset: int | None = None
    ) -> dict[str, Any]:
        """Read aggregated output of a background session."""
        args: dict[str, Any] = {"action": "log", "sessionId": session_id, "limit": limit}
        if offset is not None:
            args["offset"] = offset
        return await self._invoke("process", args)

    async def kill_process(self, session_id: str) -> dict[str, Any]:
        """Terminate a background session."""
        return await self._invoke("process", {"action": "kill", "sessionId": session_id})

    async def list_processes(self) -> list[dict[str, Any]]:
        """List running and finished background sessions."""
        result = await self._invoke("process", {"action": "list"})
        return result.get("sessions", result) if isinstance(result, dict) else []

    # ── file tools (read / write / edit) ────────────────────

    async def write_file(self, path: str, content: str) -> dict[str, Any]:
        """Write content to a file on the Gateway workspace."""
        return await self._invoke("write", {"path": path, "content": content})

    async def read_file(self, path: str) -> str:
        """Read a file from the Gateway workspace."""
        result = await self._invoke("read", {"path": path})
        if isinstance(result, dict):
            return result.get("content", str(result))
        return str(result)

    async def edit_file(
        self, path: str, old_string: str, new_string: str
    ) -> dict[str, Any]:
        """Replace text in a file on the Gateway workspace."""
        return await self._invoke("edit", {
            "path": path,
            "old_string": old_string,
            "new_string": new_string,
        })

    # ── web_fetch tool ───────────────────────────────────────

    async def web_fetch(self, url: str, max_chars: int = 30_000) -> str:
        """Fetch a URL via the Gateway and return extracted text/markdown."""
        result = await self._invoke(
            "web_fetch", {"url": url, "extractMode": "markdown", "maxChars": max_chars}
        )
        return result.get("content", str(result)) if isinstance(result, dict) else str(result)

    # ── health / capability check ────────────────────────────

    async def health_check(self) -> bool:
        """Quick ping: check session status to verify connectivity."""
        try:
            await self._invoke("session_status", {})
            return True
        except Exception:
            return False

    async def check_coding_tools(self) -> dict[str, bool]:
        """Probe which coding tools are available on this Gateway.

        Returns a dict like {"exec": True, "write": False, ...}.
        """
        tools_to_check = ["exec", "read", "write", "edit", "process", "web_fetch"]
        results: dict[str, bool] = {}

        for tool_name in tools_to_check:
            try:
                payload: dict[str, Any] = {"tool": tool_name}
                resp = await self._client.post(_INVOKE_PATH, json=payload)
                data = resp.json()
                err = data.get("error", {})
                # "not_found" means tool is disabled; any other error means it's available
                results[tool_name] = err.get("type") != "not_found"
            except Exception:
                results[tool_name] = False

        logger.info("openclaw_coding_tools_check", results=results)
        return results


class OpenClawGatewayError(Exception):
    """Error from the OpenClaw Gateway."""

    def __init__(self, message: str, tool: str = ""):
        super().__init__(message)
        self.tool = tool


_client: OpenClawGatewayClient | None = None


async def get_gateway_client() -> OpenClawGatewayClient:
    global _client
    if _client is None:
        _client = OpenClawGatewayClient()
    return _client


async def close_gateway_client() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None
