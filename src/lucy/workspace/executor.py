"""Code execution for Lucy workspaces.

Three execution paths, tried in priority order:

1. **OpenClaw Gateway** (preferred) — your own VPS via the Tools Invoke API.
   Persistent filesystem, background process support, no cold-start penalty.
   Supports foreground commands and long-running background jobs.
2. **Local subprocess** — runs in the Lucy server process, restricted to the
   workspace scripts/ directory. Secrets-stripped environment.
3. **Composio sandbox** (last resort) — ephemeral Docker container on Composio's
   infrastructure. Stateless, no installed packages persist between calls.
   Used only when both Gateway and local subprocess are unavailable.

The LLM can also call Gateway tools directly through the agent loop
(lucy_exec_command, lucy_start_background, lucy_poll_process), which doesn't
go through this module at all. This module is for *programmatic* execution
from cron scripts, data collection tasks, etc.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass

import structlog

from lucy.workspace.filesystem import get_workspace

logger = structlog.get_logger()

MAX_OUTPUT_CHARS = 200_000
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
    """Execute Python code, preferring the OpenClaw Gateway.

    Priority: Gateway → local subprocess → Composio sandbox (last resort).

    Args:
        workspace_id: Workspace context for local fallback CWD.
        code: Python source code to run.
        timeout: Max execution time in seconds.
    """
    t0 = time.monotonic()

    # 1. OpenClaw Gateway (your VPS — persistent, fast)
    result = await _execute_via_gateway(f"python3 -c {_shell_quote(code)}", timeout)
    if result is not None:
        result.elapsed_ms = round((time.monotonic() - t0) * 1000)
        result.method = "gateway_python"
        return result

    # 2. Local subprocess (secrets-stripped, sandboxed to workspace)
    result = await _execute_local_python(workspace_id, code, timeout)
    result.elapsed_ms = round((time.monotonic() - t0) * 1000)
    if result.success:
        return result

    # 3. Composio sandbox (last resort — stateless, external)
    composio_result = await _execute_via_composio(
        workspace_id, code, language="python", timeout=timeout
    )
    if composio_result is not None:
        composio_result.elapsed_ms = round((time.monotonic() - t0) * 1000)
        return composio_result

    return result


async def execute_bash(
    workspace_id: str,
    command: str,
    timeout: int = SUBPROCESS_TIMEOUT,
) -> ExecutionResult:
    """Execute a bash command, preferring the OpenClaw Gateway.

    Priority: Gateway → local subprocess → Composio sandbox (last resort).
    """
    t0 = time.monotonic()

    # 1. OpenClaw Gateway (your VPS)
    result = await _execute_via_gateway(command, timeout)
    if result is not None:
        result.elapsed_ms = round((time.monotonic() - t0) * 1000)
        return result

    # 2. Local subprocess (restricted env, no secrets)
    result = await _execute_local_bash(command, timeout, workspace_id=workspace_id)
    result.elapsed_ms = round((time.monotonic() - t0) * 1000)
    if result.success:
        return result

    # 3. Composio sandbox (last resort)
    composio_result = await _execute_via_composio(
        workspace_id, command, language="bash", timeout=timeout
    )
    if composio_result is not None:
        composio_result.elapsed_ms = round((time.monotonic() - t0) * 1000)
        return composio_result

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

    scripts_dir = ws.root.resolve() / "scripts"
    if not full_path.is_relative_to(scripts_dir):
        return ExecutionResult(
            success=False,
            output="",
            error="Scripts must be in the scripts/ directory",
            exit_code=1,
            method="local",
        )

    code = full_path.read_text("utf-8")

    # 1. OpenClaw Gateway
    gateway_result = await _execute_via_gateway(
        f"python3 {_shell_quote(str(full_path))}{' ' + ' '.join(args) if args else ''}",
        timeout,
    )
    if gateway_result is not None:
        gateway_result.elapsed_ms = round((time.monotonic() - t0) * 1000)
        return gateway_result

    # 2. Local subprocess fallback
    cmd = ["python3", str(full_path)] + (args or [])
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(ws.root),
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise

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
    except TimeoutError:
        return ExecutionResult(
            success=False,
            output="",
            error=f"Script timed out after {timeout}s",
            exit_code=124,
            elapsed_ms=round((time.monotonic() - t0) * 1000),
            method="local_script",
        )


# ── OpenClaw Gateway (primary execution path) ─────────────────────────

def _shell_quote(s: str) -> str:
    """Wrap a string in single quotes for safe shell embedding."""
    import shlex
    return shlex.quote(s)


async def _execute_via_gateway(
    command: str,
    timeout: int = SUBPROCESS_TIMEOUT,
) -> ExecutionResult | None:
    """Execute a shell command on the OpenClaw Gateway VPS.

    Returns None if the Gateway is unavailable or not configured — callers
    should fall back to local subprocess in that case.
    """
    try:
        from lucy.integrations.openclaw_gateway import OpenClawGatewayError, get_gateway_client

        client = await get_gateway_client()
        result = await client.exec_command(command, timeout=timeout)

        output = str(result.get("output") or result.get("stdout") or "")
        error = str(result.get("error") or result.get("stderr") or "")
        exit_code = int(result.get("exit_code") or result.get("exitCode") or 0)
        success = exit_code == 0 and not result.get("error")

        return ExecutionResult(
            success=success,
            output=output[:MAX_OUTPUT_CHARS],
            error=error[:MAX_OUTPUT_CHARS],
            exit_code=exit_code,
            method="gateway_bash",
        )

    except (ImportError, RuntimeError):
        # Gateway not configured — fall through to next execution path
        return None
    except OpenClawGatewayError as e:
        logger.warning("gateway_exec_failed", error=str(e), command=command[:120])
        return None
    except Exception as e:
        logger.warning("gateway_exec_unavailable", error=str(e))
        return None


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
        if not client._ensure_sdk():
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

def _sanitized_subprocess_env() -> dict[str, str]:
    """Build a subprocess environment that strips Lucy's secrets.

    The local executor fallback spawns a child process that inherits the
    full environment of the Lucy server process — including all API keys,
    OAuth tokens, and database credentials in LUCY_* environment variables.
    A malicious or buggy script could exfiltrate these via HTTP or write
    them to a file.

    This function returns a minimal environment suitable for running
    user-provided scripts: only standard system variables are preserved.
    Lucy-specific secrets (LUCY_*, OPENROUTER_*, COMPOSIO_*, SLACK_*,
    DATABASE_URL, etc.) are removed.
    """
    _SECRET_PREFIXES = (
        "LUCY_", "OPENROUTER_", "COMPOSIO_", "SLACK_", "AGENTMAIL_",
        "VERCEL_", "CONVEX_", "DATABASE_", "REDIS_", "SENTRY_",
        "AWS_", "GCP_", "AZURE_", "ANTHROPIC_", "OPENAI_",
    )
    safe: dict[str, str] = {}
    for key, value in os.environ.items():
        if any(key.upper().startswith(p) for p in _SECRET_PREFIXES):
            continue
        safe[key] = value
    # Ensure clean Python environment
    safe["PYTHONDONTWRITEBYTECODE"] = "1"
    safe.pop("PYTHONSTARTUP", None)
    safe.pop("PYTHONPATH", None)
    return safe


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
            env=_sanitized_subprocess_env(),
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise

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

    except TimeoutError:
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
    *,
    workspace_id: str | None = None,
) -> ExecutionResult:
    """Execute a bash command via local subprocess."""
    cwd: str | None = None
    if workspace_id:
        ws = get_workspace(workspace_id)
        cwd = str(ws.root)
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=_sanitized_subprocess_env(),
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise

        output = stdout.decode("utf-8", errors="replace")[:MAX_OUTPUT_CHARS]
        err = stderr.decode("utf-8", errors="replace")[:MAX_OUTPUT_CHARS]

        return ExecutionResult(
            success=proc.returncode == 0,
            output=output,
            error=err,
            exit_code=proc.returncode or 0,
            method="local_bash",
        )

    except TimeoutError:
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
