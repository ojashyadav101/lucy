"""OpenClaw Gateway tools — direct VPS execution for the agent.

Exposes the Gateway's foreground execution and background process management
as first-class lucy_* agent tools. These are preferred over
COMPOSIO_REMOTE_BASH_TOOL for all tasks that benefit from a persistent
working directory, installed packages, or long-running processes.

Routing guidance (also in prompts/modules/tool_use.md):
  - lucy_exec_command   → foreground shell command on the VPS (git, npm, python, etc.)
  - lucy_start_background → spawn a long-running background process
  - lucy_poll_process   → check output / status of a background process
  - COMPOSIO_REMOTE_BASH_TOOL → reserved for isolated throwaway sandboxes only
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger()

_GATEWAY_TOOLS: frozenset[str] = frozenset({
    "lucy_exec_command",
    "lucy_start_background",
    "lucy_poll_process",
})


def get_gateway_tool_definitions() -> list[dict[str, Any]]:
    """Return OpenAI-format tool definitions for Gateway execution tools."""
    return [
        {
            "type": "function",
            "function": {
                "name": "lucy_exec_command",
                "description": (
                    "Run a foreground shell command on the VPS (OpenClaw Gateway). "
                    "THIS IS THE ONLY TOOL YOU SHOULD USE FOR SHELL COMMANDS — do NOT use "
                    "COMPOSIO_REMOTE_BASH_TOOL when this tool is available. "
                    "The VPS has a persistent filesystem, "
                    "installed packages survive between calls, and working directories are maintained.\n\n"
                    "Use for: git clone, npm install, pip install, python scripts, database "
                    "connections, file operations, build commands, curl/wget, mongosh, psql, etc.\n\n"
                    "Returns stdout, stderr, and exit code.\n\n"
                    "NEVER use COMPOSIO_REMOTE_BASH_TOOL instead of this — it is an isolated "
                    "throwaway sandbox with no persistent state and should only be used for "
                    "untrusted third-party code that must be sandboxed."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": (
                                "The shell command to run. Can be any valid bash command. "
                                "E.g.: 'git clone https://github.com/org/repo.git', "
                                "'npm install', 'python3 script.py', 'mongosh \"mongodb+srv://...\" --eval \"...\"'"
                            ),
                        },
                        "workdir": {
                            "type": "string",
                            "description": (
                                "Working directory on the VPS (optional). "
                                "Use this to run commands in a cloned repo or project folder."
                            ),
                        },
                        "timeout": {
                            "type": "integer",
                            "description": "Max execution time in seconds (default: 60, max: 300).",
                        },
                        "env": {
                            "type": "object",
                            "description": (
                                "Additional environment variables as key-value pairs (optional). "
                                "E.g.: {\"NODE_ENV\": \"production\"}"
                            ),
                            "additionalProperties": {"type": "string"},
                        },
                    },
                    "required": ["command"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "lucy_start_background",
                "description": (
                    "Start a long-running shell command in the background on the VPS. "
                    "Returns a session_id you can use with lucy_poll_process to check output.\n\n"
                    "Use for: starting servers, long-running builds, data collection scripts "
                    "that take several minutes, or anything that should keep running while "
                    "you do other things.\n\n"
                    "For always-on services (webhook listeners, workers), use lucy_start_service instead."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "The shell command to run in the background.",
                        },
                        "workdir": {
                            "type": "string",
                            "description": "Working directory on the VPS (optional).",
                        },
                        "env": {
                            "type": "object",
                            "description": "Extra environment variables (optional).",
                            "additionalProperties": {"type": "string"},
                        },
                    },
                    "required": ["command"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "lucy_poll_process",
                "description": (
                    "Poll a background process for new output and exit status. "
                    "Use after lucy_start_background to check if the command completed, "
                    "read its output, or detect errors.\n\n"
                    "Returns: output so far, whether the process is still running, and exit code if done."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "session_id": {
                            "type": "string",
                            "description": "The session_id returned by lucy_start_background.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max number of log lines to return (default: 100).",
                        },
                    },
                    "required": ["session_id"],
                },
            },
        },
    ]


def is_gateway_tool(tool_name: str) -> bool:
    """Check if a tool name belongs to the gateway module."""
    return tool_name in _GATEWAY_TOOLS


async def execute_gateway_tool(
    tool_name: str,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    """Execute a Gateway tool call."""
    from lucy.integrations.openclaw_gateway import OpenClawGatewayError, get_gateway_client

    try:
        client = await get_gateway_client()
    except RuntimeError as e:
        return {
            "error": (
                f"OpenClaw Gateway is not configured: {e}. "
                "Falling back to COMPOSIO_REMOTE_BASH_TOOL for this command."
            )
        }

    if tool_name == "lucy_exec_command":
        command = parameters.get("command", "").strip()
        if not command:
            return {"error": "command is required"}

        timeout = min(int(parameters.get("timeout") or 60), 300)
        workdir = parameters.get("workdir") or None
        env = parameters.get("env") or None

        try:
            result = await client.exec_command(
                command=command,
                timeout=timeout,
                workdir=workdir,
                env=env,
            )
            output = str(result.get("output") or result.get("stdout") or "")
            error = str(result.get("error") or result.get("stderr") or "")
            exit_code = int(result.get("exit_code") or result.get("exitCode") or 0)

            logger.info(
                "gateway_exec_command",
                command=command[:80],
                exit_code=exit_code,
                output_len=len(output),
            )
            return {
                "success": exit_code == 0,
                "output": output[:50_000],
                "error": error[:5_000],
                "exit_code": exit_code,
            }
        except OpenClawGatewayError as e:
            logger.warning("gateway_exec_command_failed", command=command[:80], error=str(e))
            return {"error": str(e), "fallback_hint": "Try COMPOSIO_REMOTE_BASH_TOOL if gateway unavailable."}

    elif tool_name == "lucy_start_background":
        command = parameters.get("command", "").strip()
        if not command:
            return {"error": "command is required"}

        workdir = parameters.get("workdir") or None
        env = parameters.get("env") or None

        try:
            session_id = await client.start_background(
                command=command,
                workdir=workdir,
                env=env,
            )
            logger.info("gateway_background_started", command=command[:80], session_id=session_id)
            return {
                "session_id": session_id,
                "status": "running",
                "message": (
                    f"Background process started. Use lucy_poll_process with "
                    f"session_id='{session_id}' to check progress."
                ),
            }
        except OpenClawGatewayError as e:
            logger.warning("gateway_background_failed", command=command[:80], error=str(e))
            return {"error": str(e)}

    elif tool_name == "lucy_poll_process":
        session_id = parameters.get("session_id", "").strip()
        if not session_id:
            return {"error": "session_id is required"}

        limit = int(parameters.get("limit") or 100)

        try:
            result = await client.log_process(session_id=session_id, limit=limit)
            logger.info("gateway_process_polled", session_id=session_id)
            return result
        except OpenClawGatewayError as e:
            logger.warning("gateway_poll_failed", session_id=session_id, error=str(e))
            return {"error": str(e)}

    return {"error": f"Unknown gateway tool: {tool_name}"}
