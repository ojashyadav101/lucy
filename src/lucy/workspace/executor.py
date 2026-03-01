"""Code execution for Lucy workspaces — V2 (local-first).

Three execution paths, tried in order:

1. **Local subprocess** (preferred) — runs Python/bash in the codespace.
   Fastest path with zero API latency.
2. **Composio sandbox** (fallback) — COMPOSIO_REMOTE_WORKBENCH for Python,
   COMPOSIO_REMOTE_BASH_TOOL for shell. Fully isolated Docker sandbox.
   Used when local execution is unavailable or explicitly requested.

The LLM calls lucy_execute_python / lucy_execute_bash, which route through
code_executor.py's validation pipeline, then execute here.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from lucy.workspace.filesystem import WorkspaceFS, get_workspace

logger = structlog.get_logger()

MAX_OUTPUT_CHARS = 50_000
SUBPROCESS_TIMEOUT = 60

# Track installed packages to avoid repeated pip installs
_installed_packages: set[str] = set()


@dataclass
class ExecutionResult:
    """Result of a code execution."""

    success: bool
    output: str
    error: str = ""
    exit_code: int = 0
    elapsed_ms: int = 0
    method: str = ""
    files_created: list[str] = field(default_factory=list)


async def execute_python(
    workspace_id: str,
    code: str,
    timeout: int = SUBPROCESS_TIMEOUT,
) -> ExecutionResult:
    """Execute Python code, preferring LOCAL execution.

    V2 change: local-first instead of Composio-first.
    This eliminates API latency for code execution.

    Args:
        workspace_id: Workspace context.
        code: Python source code to run.
        timeout: Max execution time in seconds.
    """
    t0 = time.monotonic()

    # Try local subprocess FIRST (fast, no API call)
    result = await _execute_local_python(workspace_id, code, timeout)
    if result.success or result.exit_code != 127:
        # Succeeded, or failed with a real error (not "command not found")
        result.elapsed_ms = round((time.monotonic() - t0) * 1000)
        return result

    # Fallback to Composio sandbox only if local is broken
    logger.info(
        "local_python_unavailable_falling_back",
        workspace_id=workspace_id,
        local_error=result.error[:200],
    )
    composio_result = await _execute_via_composio(
        workspace_id, code, language="python", timeout=timeout
    )
    if composio_result is not None:
        composio_result.elapsed_ms = round((time.monotonic() - t0) * 1000)
        return composio_result

    # Both failed — return local result (has more useful error info)
    result.elapsed_ms = round((time.monotonic() - t0) * 1000)
    return result


async def execute_bash(
    workspace_id: str,
    command: str,
    timeout: int = SUBPROCESS_TIMEOUT,
) -> ExecutionResult:
    """Execute a bash command, preferring LOCAL execution."""
    t0 = time.monotonic()

    # Local first
    result = await _execute_local_bash(command, timeout)
    if result.success or result.exit_code != 127:
        result.elapsed_ms = round((time.monotonic() - t0) * 1000)
        return result

    # Composio fallback
    composio_result = await _execute_via_composio(
        workspace_id, command, language="bash", timeout=timeout
    )
    if composio_result is not None:
        composio_result.elapsed_ms = round((time.monotonic() - t0) * 1000)
        return composio_result

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

    # Always run scripts locally
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


# ── Package auto-install ───────────────────────────────────────────────

async def auto_install_package(package_name: str) -> bool:
    """Attempt to install a missing Python package via pip.

    Returns True if installation succeeded.
    """
    # Normalize common package name differences
    _PACKAGE_ALIASES = {
        "cv2": "opencv-python",
        "PIL": "Pillow",
        "yaml": "pyyaml",
        "bs4": "beautifulsoup4",
        "dateutil": "python-dateutil",
        "sklearn": "scikit-learn",
        "dotenv": "python-dotenv",
    }

    install_name = _PACKAGE_ALIASES.get(package_name, package_name)

    if install_name in _installed_packages:
        return True

    logger.info("auto_installing_package", package=install_name)

    try:
        proc = await asyncio.create_subprocess_exec(
            "pip", "install", "--quiet", install_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)

        if proc.returncode == 0:
            _installed_packages.add(install_name)
            logger.info("package_installed", package=install_name)
            return True
        else:
            err = stderr.decode("utf-8", errors="replace")[:500]
            logger.warning("package_install_failed", package=install_name, error=err)
            return False

    except (asyncio.TimeoutError, Exception) as e:
        logger.warning("package_install_error", package=install_name, error=str(e))
        return False


# ── Composio sandbox (fallback) ────────────────────────────────────────

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


# ── Local subprocess (primary) ─────────────────────────────────────────

async def _execute_local_python(
    workspace_id: str,
    code: str,
    timeout: int = SUBPROCESS_TIMEOUT,
) -> ExecutionResult:
    """Execute Python via local subprocess."""
    ws = get_workspace(workspace_id)

    # Track files before execution for detecting new outputs
    scripts_dir = ws.root / "scripts"
    files_before = set()
    try:
        if scripts_dir.exists():
            files_before = {str(p) for p in scripts_dir.rglob("*") if p.is_file()}
    except Exception:
        pass

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

        # Detect newly created files
        files_created: list[str] = []
        try:
            if scripts_dir.exists():
                files_after = {str(p) for p in scripts_dir.rglob("*") if p.is_file()}
                new_files = files_after - files_before
                files_created = sorted(new_files)[:10]  # Cap at 10
        except Exception:
            pass

        logger.info(
            "local_python_executed",
            workspace_id=workspace_id,
            exit_code=proc.returncode,
            output_len=len(output),
            method="local_python",
        )
        return ExecutionResult(
            success=proc.returncode == 0,
            output=output,
            error=err,
            exit_code=proc.returncode or 0,
            method="local_python",
            files_created=files_created,
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
