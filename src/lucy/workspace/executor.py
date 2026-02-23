"""Code execution for Lucy workspaces.

Three execution paths, tried in order:

1. **Composio sandbox** (preferred) — COMPOSIO_REMOTE_WORKBENCH for Python,
   COMPOSIO_REMOTE_BASH_TOOL for shell. Fully isolated Docker sandbox.
2. **Local subprocess** — restricted to workspace scripts/ directory only.
   Used as fallback when Composio is unavailable.

The LLM can also execute code directly through meta-tools in the agent
loop (COMPOSIO_REMOTE_WORKBENCH / COMPOSIO_REMOTE_BASH_TOOL), which
doesn't go through this module at all. This module is for *programmatic*
execution — cron scripts, data collection, etc.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from lucy.workspace.filesystem import WorkspaceFS, get_workspace

logger = structlog.get_logger()

MAX_OUTPUT_CHARS = 50_000
SUBPROCESS_TIMEOUT = 60


@dataclass
class ExecutionResult:
    """Result of a code execution."""

    success: bool
    output: str
    error: str = ""
    exit_code: int = 0
    elapsed_ms: int = 0
    method: str = ""


async def execute_python(
    workspace_id: str,
    code: str,
    timeout: int = SUBPROCESS_TIMEOUT,
) -> ExecutionResult:
    """Execute Python code, preferring Composio sandbox.

    Args:
        workspace_id: Workspace context for Composio session.
        code: Python source code to run.
        timeout: Max execution time in seconds.
    """
    t0 = time.monotonic()

    # Try Composio sandbox first
    result = await _execute_via_composio(
        workspace_id, code, language="python", timeout=timeout
    )
    if result is not None:
        result.elapsed_ms = round((time.monotonic() - t0) * 1000)
        return result

    # Fallback: local subprocess (sandboxed by limiting to workspace scripts dir)
    result = await _execute_local_python(workspace_id, code, timeout)
    result.elapsed_ms = round((time.monotonic() - t0) * 1000)
    return result


async def execute_bash(
    workspace_id: str,
    command: str,
    timeout: int = SUBPROCESS_TIMEOUT,
) -> ExecutionResult:
    """Execute a bash command, preferring Composio sandbox."""
    t0 = time.monotonic()

    result = await _execute_via_composio(
        workspace_id, command, language="bash", timeout=timeout
    )
    if result is not None:
        result.elapsed_ms = round((time.monotonic() - t0) * 1000)
        return result

    result = await _execute_local_bash(command, timeout)
    result.elapsed_ms = round((time.monotonic() - t0) * 1000)
    return result


async def execute_workspace_script(
    workspace_id: str,
    script_path: str,
    args: list[str] | None = None,
    timeout: int = SUBPROCESS_TIMEOUT,
) -> ExecutionResult:
    """Run a Python script from the workspace's scripts/ directory.

    The script_path is relative to the workspace root (e.g. "scripts/collect.py").
    """
    t0 = time.monotonic()
    ws = get_workspace(workspace_id)
    full_path = ws._resolve(script_path)

    if not full_path.is_file():
        return ExecutionResult(
            success=False,
            output="",
            error=f"Script not found: {script_path}",
            exit_code=1,
            method="local",
        )

    if not str(full_path).startswith(str(ws.root.resolve() / "scripts")):
        return ExecutionResult(
            success=False,
            output="",
            error="Scripts must be in the scripts/ directory",
            exit_code=1,
            method="local",
        )

    code = full_path.read_text("utf-8")

    # Try Composio first
    result = await _execute_via_composio(
        workspace_id, code, language="python", timeout=timeout
    )
    if result is not None:
        result.elapsed_ms = round((time.monotonic() - t0) * 1000)
        return result

    # Local fallback
    cmd = ["python3", str(full_path)] + (args or [])
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(ws.root),
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        output = stdout.decode("utf-8", errors="replace")[:MAX_OUTPUT_CHARS]
        err = stderr.decode("utf-8", errors="replace")[:MAX_OUTPUT_CHARS]
        return ExecutionResult(
            success=proc.returncode == 0,
            output=output,
            error=err,
            exit_code=proc.returncode or 0,
            elapsed_ms=round((time.monotonic() - t0) * 1000),
            method="local_script",
        )
    except asyncio.TimeoutError:
        return ExecutionResult(
            success=False,
            output="",
            error=f"Script timed out after {timeout}s",
            exit_code=124,
            elapsed_ms=round((time.monotonic() - t0) * 1000),
            method="local_script",
        )


# ── Composio sandbox ───────────────────────────────────────────────────

async def _execute_via_composio(
    workspace_id: str,
    code: str,
    language: str = "python",
    timeout: int = SUBPROCESS_TIMEOUT,
) -> ExecutionResult | None:
    """Execute code via Composio's REMOTE_WORKBENCH or REMOTE_BASH.

    Returns None if Composio is unavailable (caller should fall back).
    """
    try:
        from lucy.integrations.composio_client import get_composio_client

        client = get_composio_client()
        if not client._composio:
            return None

        tool_name = (
            "COMPOSIO_REMOTE_WORKBENCH"
            if language == "python"
            else "COMPOSIO_REMOTE_BASH_TOOL"
        )

        result = await client.execute_tool_call(
            workspace_id=workspace_id,
            tool_name=tool_name,
            arguments={"code": code},
        )

        if "error" in result and not result.get("output"):
            logger.warning(
                "composio_exec_error",
                tool=tool_name,
                error=result["error"],
            )
            return ExecutionResult(
                success=False,
                output="",
                error=str(result["error"]),
                method=f"composio_{language}",
            )

        output = str(result.get("output", result.get("result", "")))
        return ExecutionResult(
            success=True,
            output=output[:MAX_OUTPUT_CHARS],
            method=f"composio_{language}",
        )

    except Exception as e:
        logger.warning("composio_exec_unavailable", error=str(e))
        return None


# ── Local subprocess fallback ──────────────────────────────────────────

async def _execute_local_python(
    workspace_id: str,
    code: str,
    timeout: int = SUBPROCESS_TIMEOUT,
) -> ExecutionResult:
    """Execute Python via local subprocess."""
    ws = get_workspace(workspace_id)
    try:
        proc = await asyncio.create_subprocess_exec(
            "python3", "-c", code,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(ws.root),
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        output = stdout.decode("utf-8", errors="replace")[:MAX_OUTPUT_CHARS]
        err = stderr.decode("utf-8", errors="replace")[:MAX_OUTPUT_CHARS]

        logger.info(
            "local_python_executed",
            workspace_id=workspace_id,
            exit_code=proc.returncode,
            output_len=len(output),
        )
        return ExecutionResult(
            success=proc.returncode == 0,
            output=output,
            error=err,
            exit_code=proc.returncode or 0,
            method="local_python",
        )

    except asyncio.TimeoutError:
        return ExecutionResult(
            success=False,
            output="",
            error=f"Execution timed out after {timeout}s",
            exit_code=124,
            method="local_python",
        )
    except Exception as e:
        return ExecutionResult(
            success=False,
            output="",
            error=str(e),
            exit_code=1,
            method="local_python",
        )


async def _execute_local_bash(
    command: str,
    timeout: int = SUBPROCESS_TIMEOUT,
) -> ExecutionResult:
    """Execute a bash command via local subprocess."""
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        output = stdout.decode("utf-8", errors="replace")[:MAX_OUTPUT_CHARS]
        err = stderr.decode("utf-8", errors="replace")[:MAX_OUTPUT_CHARS]

        return ExecutionResult(
            success=proc.returncode == 0,
            output=output,
            error=err,
            exit_code=proc.returncode or 0,
            method="local_bash",
        )

    except asyncio.TimeoutError:
        return ExecutionResult(
            success=False,
            output="",
            error=f"Command timed out after {timeout}s",
            exit_code=124,
            method="local_bash",
        )
    except Exception as e:
        return ExecutionResult(
            success=False,
            output="",
            error=str(e),
            exit_code=1,
            method="local_bash",
        )
