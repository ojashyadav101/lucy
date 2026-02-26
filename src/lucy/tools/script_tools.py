"""Script execution tools — the script-first pipeline for data tasks.

Provides ``lucy_run_script``, a tool that:
  1. Injects API keys from keys.json as env vars.
  2. Executes Python scripts via workspace/executor.
  3. Scans output for generated files.
  4. Auto-uploads files to Slack.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import structlog

from lucy.config import settings

logger = structlog.get_logger()

_MAX_SCRIPT_TIMEOUT = 600
_DEFAULT_SCRIPT_TIMEOUT = 120
_STDOUT_MAX_CHARS = 8_000


def get_script_tool_definitions() -> list[dict[str, Any]]:
    """Return tool definitions for script execution."""
    return [
        {
            "type": "function",
            "function": {
                "name": "lucy_run_script",
                "description": (
                    "Execute a Python script for data processing, file generation, "
                    "or workflow automation. USE THIS TOOL when: "
                    "(1) processing data saved to JSON overflow files, "
                    "(2) merging/de-duplicating data from multiple sources, "
                    "(3) generating Excel/CSV/JSON output files, "
                    "(4) fetching 100+ records via API. "
                    "API keys are auto-injected as env vars "
                    "(e.g. CLERK_API_KEY, POLARSH_API_KEY). "
                    "Available libraries: httpx, openpyxl, json, csv, asyncio. "
                    "Generated files are auto-uploaded to Slack. "
                    "CRITICAL: When tool results say 'DATA SAVED' with a file path, "
                    "call this tool with a script that reads those files and "
                    "processes them."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "script": {
                            "type": "string",
                            "description": (
                                "The Python script to execute. Must be self-contained. "
                                "Use httpx for HTTP calls, openpyxl for Excel generation. "
                                "Print a validation summary at the end."
                            ),
                        },
                        "timeout": {
                            "type": "integer",
                            "description": (
                                "Max execution time in seconds. Default 120, max 600. "
                                "Set higher for large data fetches."
                            ),
                        },
                        "description": {
                            "type": "string",
                            "description": "Short description of what this script does (for logging).",
                        },
                    },
                    "required": ["script"],
                },
            },
        },
    ]


def _load_api_keys() -> dict[str, str]:
    """Load API keys from keys.json and return as env var mapping."""
    keys_path = Path(settings.workspace_root).parent / "keys.json"
    env_vars: dict[str, str] = {}

    if not keys_path.exists():
        return env_vars

    try:
        keys_data = json.loads(keys_path.read_text(encoding="utf-8"))
        ci = keys_data.get("custom_integrations", {})
        for slug, entry in ci.items():
            key_val = entry.get("api_key", "") if isinstance(entry, dict) else str(entry)
            if key_val:
                env_name = f"{slug.upper()}_API_KEY"
                env_vars[env_name] = key_val
    except Exception as e:
        logger.warning("script_tools_load_keys_failed", error=str(e))

    return env_vars


def _scan_for_generated_files(stdout: str) -> list[Path]:
    """Scan script stdout for file paths that were generated."""
    patterns = [
        re.compile(r"(?:saved|wrote|generated|created|output)[:\s]+([^\s]+\.(?:xlsx|csv|json|pdf))", re.IGNORECASE),
        re.compile(r"(/tmp/[^\s]+\.(?:xlsx|csv|json|pdf))"),
        re.compile(r"([^\s]+\.(?:xlsx|csv|json|pdf))\s+(?:saved|written|generated|created)", re.IGNORECASE),
    ]
    found: list[Path] = []
    for pattern in patterns:
        for match in pattern.finditer(stdout):
            p = Path(match.group(1))
            if p.exists() and p not in found:
                found.append(p)
    return found


async def _execute_script_locally(
    code: str,
    workspace_id: str,
    timeout: int,
) -> Any:
    """Execute Python code via local subprocess.

    Uses sys.executable to ensure the same Python/venv that Lucy
    runs in is used, so installed packages (openpyxl, httpx) are
    available. Also ensures access to local overflow data files.
    """
    import asyncio
    import sys

    from lucy.workspace.filesystem import get_workspace

    ws = get_workspace(workspace_id)
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", code,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(ws.root),
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
        from lucy.workspace.executor import ExecutionResult
        return ExecutionResult(
            success=proc.returncode == 0,
            output=stdout_bytes.decode("utf-8", errors="replace")[:50_000],
            error=stderr_bytes.decode("utf-8", errors="replace")[:50_000],
            exit_code=proc.returncode or 0,
            method="local_script",
        )
    except asyncio.TimeoutError:
        from lucy.workspace.executor import ExecutionResult
        return ExecutionResult(
            success=False,
            output="",
            error=f"Script execution timed out after {timeout}s",
            exit_code=-1,
            method="local_script_timeout",
        )
    except Exception as e:
        from lucy.workspace.executor import ExecutionResult
        return ExecutionResult(
            success=False,
            output="",
            error=f"Script execution failed: {e}",
            exit_code=-1,
            method="local_script_error",
        )


async def execute_script_tool(
    parameters: dict[str, Any],
    workspace_id: str,
    slack_client: Any | None = None,
    channel_id: str | None = None,
    thread_ts: str | None = None,
) -> dict[str, Any]:
    """Execute lucy_run_script: inject keys, run locally, scan files, upload.

    Always uses local subprocess execution to ensure access to local
    overflow data files and installed packages (openpyxl, httpx, etc.).
    """
    script = parameters.get("script", "")
    if not script.strip():
        return {"error": "script parameter is required and cannot be empty"}

    timeout = min(
        parameters.get("timeout", _DEFAULT_SCRIPT_TIMEOUT),
        _MAX_SCRIPT_TIMEOUT,
    )
    description = parameters.get("description", "data processing script")

    env_vars = _load_api_keys()

    if env_vars:
        env_injection = "\nimport os\n"
        for k, v in env_vars.items():
            env_injection += f"os.environ.setdefault({k!r}, {v!r})\n"
        env_injection += "\n"

        if script.startswith("#!"):
            first_newline = script.index("\n")
            script = script[:first_newline + 1] + env_injection + script[first_newline + 1:]
        else:
            script = env_injection + script

    t0 = time.monotonic()
    logger.info(
        "script_execution_start",
        workspace_id=workspace_id,
        description=description,
        timeout=timeout,
        script_length=len(script),
        api_keys_injected=list(env_vars.keys()),
    )

    from lucy.coding.script_engine import execute_with_retry

    engine_result = await execute_with_retry(
        script=script,
        workspace_id=workspace_id,
        timeout=timeout,
    )

    elapsed_ms = round((time.monotonic() - t0) * 1000)

    stdout = engine_result.get("stdout", "")
    stderr = engine_result.get("stderr", "")

    generated_files = _scan_for_generated_files(stdout)

    uploaded_files: list[str] = []
    upload_failed_files: list[str] = []
    if generated_files and slack_client and channel_id:
        from lucy.tools.file_generator import upload_file_to_slack
        for fp in generated_files:
            try:
                upload_result = await upload_file_to_slack(
                    slack_client=slack_client,
                    file_path=fp,
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    title=fp.name,
                )
                if upload_result:
                    uploaded_files.append(fp.name)
            except Exception as e:
                logger.warning(
                    "script_file_upload_failed",
                    file=str(fp),
                    error=str(e),
                )
                upload_failed_files.append(str(fp))

    full_stdout_len = len(stdout)
    if len(stdout) > _STDOUT_MAX_CHARS:
        stdout = stdout[:_STDOUT_MAX_CHARS] + f"\n... (truncated, {full_stdout_len} total chars)"

    response: dict[str, Any] = {
        "success": engine_result.get("success", False),
        "exit_code": engine_result.get("exit_code", -1),
        "stdout": stdout,
        "elapsed_ms": elapsed_ms,
        "description": description,
        "attempts": engine_result.get("attempts", 1),
    }

    fixes = engine_result.get("fixes_applied", [])
    if fixes:
        response["fixes_applied"] = fixes

    if stderr:
        response["stderr"] = stderr[:2000]
    if generated_files:
        response["generated_files"] = [str(f) for f in generated_files]
    if uploaded_files:
        response["uploaded_to_slack"] = uploaded_files
    if upload_failed_files:
        response["upload_failed"] = upload_failed_files
        response["upload_note"] = (
            "File upload to Slack failed (likely missing files:write scope). "
            "Tell the user the file was generated and use lucy_generate_excel "
            "or lucy_generate_csv to create and upload the file instead."
        )

    log_level = "info" if response["success"] else "warning"
    getattr(logger, log_level)(
        "script_execution_complete",
        workspace_id=workspace_id,
        description=description,
        exit_code=response["exit_code"],
        elapsed_ms=elapsed_ms,
        attempts=response["attempts"],
        fixes_applied=len(fixes),
        files_generated=len(generated_files),
        files_uploaded=len(uploaded_files),
        stderr_preview=stderr[:500] if stderr else "",
    )

    return response
