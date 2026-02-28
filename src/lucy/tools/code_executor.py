"""Internal tool: code execution via workspace executor.

Wraps the existing executor.py infrastructure as a lucy_* internal tool
so the LLM can execute code directly without the Composio meta-tool
round-trip. Faster, more controlled, with output formatting.

Architecture:
    lucy_execute_code → executor.py → Composio sandbox (or local fallback)
    lucy_execute_bash → executor.py → Composio sandbox (or local fallback)

Benefits over Composio meta-tool path:
    - ~200ms faster (skip SEARCH_TOOLS + MULTI_EXECUTE_TOOL overhead)
    - Output persistence: results saved to workspace for later reference
    - Output formatting: truncated + formatted for Slack readability
    - Execution tracking: logged in activity log with timing
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import structlog

from lucy.workspace.executor import (
    ExecutionResult,
    execute_bash,
    execute_python,
    execute_workspace_script,
    MAX_OUTPUT_CHARS,
)
from lucy.workspace.filesystem import get_workspace

logger = structlog.get_logger()

# ═══════════════════════════════════════════════════════════════════════════
# TOOL DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════════

def get_code_tool_definitions() -> list[dict[str, Any]]:
    """Return OpenAI-format tool definitions for code execution."""
    return [
        {
            "type": "function",
            "function": {
                "name": "lucy_execute_python",
                "description": (
                    "Execute Python code in a sandboxed environment. "
                    "Use this when you need to: compute something, process data, "
                    "run analysis, generate outputs, or test code. "
                    "The sandbox has common packages (pandas, numpy, requests, etc). "
                    "Output is captured from stdout. Use print() for results."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": "Python code to execute. Use print() for output.",
                        },
                        "description": {
                            "type": "string",
                            "description": "Brief description of what this code does (for logging).",
                        },
                        "timeout": {
                            "type": "integer",
                            "description": "Max execution time in seconds (default: 60, max: 300).",
                        },
                    },
                    "required": ["code"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "lucy_execute_bash",
                "description": (
                    "Execute a bash command in a sandboxed environment. "
                    "Use this for: shell operations, file manipulation, "
                    "system info, package installation, or piped commands."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "Bash command to execute.",
                        },
                        "description": {
                            "type": "string",
                            "description": "Brief description of what this command does.",
                        },
                        "timeout": {
                            "type": "integer",
                            "description": "Max execution time in seconds (default: 60, max: 300).",
                        },
                    },
                    "required": ["command"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "lucy_run_script",
                "description": (
                    "Run a saved Python script from the workspace's scripts/ directory. "
                    "Use this for: recurring tasks, data collection scripts, "
                    "or previously-saved workflows."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "script_path": {
                            "type": "string",
                            "description": (
                                "Path relative to workspace root, e.g. 'scripts/collect_data.py'."
                            ),
                        },
                        "args": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Command-line arguments to pass to the script.",
                        },
                        "timeout": {
                            "type": "integer",
                            "description": "Max execution time in seconds (default: 60, max: 300).",
                        },
                    },
                    "required": ["script_path"],
                },
            },
        },
    ]


# ═══════════════════════════════════════════════════════════════════════════
# TOOL EXECUTION
# ═══════════════════════════════════════════════════════════════════════════

_MAX_RESULT_CHARS = 4000  # Truncate output for LLM context window
_MAX_TIMEOUT = 300


async def execute_code_tool(
    tool_name: str,
    parameters: dict[str, Any],
    workspace_id: str = "",
) -> dict[str, Any]:
    """Execute a code execution tool and return formatted results."""

    timeout = min(parameters.get("timeout", 60), _MAX_TIMEOUT)
    description = parameters.get("description", "")

    t0 = time.monotonic()

    try:
        if tool_name == "lucy_execute_python":
            code = parameters.get("code", "")
            if not code.strip():
                return {"error": "No code provided."}

            # Security: block obviously dangerous operations
            danger = _check_dangerous_code(code)
            if danger:
                return {"error": f"Blocked: {danger}"}

            result = await execute_python(
                workspace_id=workspace_id,
                code=code,
                timeout=timeout,
            )

        elif tool_name == "lucy_execute_bash":
            command = parameters.get("command", "")
            if not command.strip():
                return {"error": "No command provided."}

            danger = _check_dangerous_bash(command)
            if danger:
                return {"error": f"Blocked: {danger}"}

            result = await execute_bash(
                workspace_id=workspace_id,
                command=command,
                timeout=timeout,
            )

        elif tool_name == "lucy_run_script":
            script_path = parameters.get("script_path", "")
            if not script_path:
                return {"error": "No script_path provided."}

            args = parameters.get("args", [])
            result = await execute_workspace_script(
                workspace_id=workspace_id,
                script_path=script_path,
                args=args,
                timeout=timeout,
            )

        else:
            return {"error": f"Unknown code tool: {tool_name}"}

    except Exception as e:
        logger.error(
            "code_tool_exception",
            tool=tool_name,
            error=str(e),
            workspace_id=workspace_id,
        )
        return {"error": f"Execution failed: {str(e)[:200]}"}

    elapsed_ms = round((time.monotonic() - t0) * 1000)

    # Format result for LLM consumption
    formatted = _format_result(result, elapsed_ms, description)

    # Log execution to workspace activity
    await _log_execution(
        workspace_id, tool_name, description, result, elapsed_ms
    )

    logger.info(
        "code_tool_executed",
        tool=tool_name,
        success=result.success,
        method=result.method,
        elapsed_ms=elapsed_ms,
        output_len=len(result.output),
        description=description,
    )

    return formatted


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _format_result(
    result: ExecutionResult,
    elapsed_ms: int,
    description: str,
) -> dict[str, Any]:
    """Format an ExecutionResult for the LLM."""
    output = result.output.strip()
    error = result.error.strip()

    # Truncate for LLM context window
    if len(output) > _MAX_RESULT_CHARS:
        output = output[:_MAX_RESULT_CHARS] + f"\n... (truncated, {len(result.output)} total chars)"

    if len(error) > 1000:
        error = error[:1000] + "\n... (truncated)"

    response: dict[str, Any] = {
        "success": result.success,
        "execution_method": result.method,
        "elapsed_ms": elapsed_ms,
    }

    if result.success:
        response["output"] = output if output else "(no output)"
    else:
        response["output"] = output if output else ""
        response["error"] = error if error else f"Exit code: {result.exit_code}"

    return response


def _check_dangerous_code(code: str) -> str | None:
    """Check Python code for obviously dangerous operations.

    This is a lightweight check, NOT a sandbox. The actual sandboxing
    is done by Composio REMOTE_WORKBENCH (Docker) or the local subprocess
    with restricted cwd.

    Returns a reason string if dangerous, None if OK.
    """
    code_lower = code.lower()

    # Block system-level destruction
    dangerous_patterns = [
        ("os.system('rm -rf", "destructive system command"),
        ("shutil.rmtree('/'", "destructive file operation"),
        ("subprocess.call(['rm'", "destructive subprocess"),
        ("import socket", "raw socket access — use requests/httpx instead"),
    ]

    for pattern, reason in dangerous_patterns:
        if pattern in code_lower:
            return reason

    return None


def _check_dangerous_bash(command: str) -> str | None:
    """Check bash commands for obviously dangerous operations."""
    cmd_lower = command.lower().strip()

    dangerous_prefixes = [
        "rm -rf /",
        "dd if=",
        "mkfs",
        ":(){:|:&};:",  # fork bomb
        "chmod -R 777 /",
    ]

    for prefix in dangerous_prefixes:
        if cmd_lower.startswith(prefix) or f" {prefix}" in cmd_lower:
            return f"blocked dangerous command: {prefix[:20]}..."

    return None


async def _log_execution(
    workspace_id: str,
    tool_name: str,
    description: str,
    result: ExecutionResult,
    elapsed_ms: int,
) -> None:
    """Log code execution to workspace activity log."""
    if not workspace_id:
        return

    try:
        from lucy.workspace.activity_log import log_activity

        status = "success" if result.success else "failed"
        detail = description or tool_name
        output_preview = result.output[:200] if result.output else ""

        await log_activity(
            workspace_id,
            category="code_execution",
            summary=f"{detail} [{status}, {elapsed_ms}ms, via {result.method}]",
            detail=output_preview if result.success else result.error[:200],
        )
    except Exception as e:
        logger.debug("activity_log_failed", error=str(e))
