"""ScriptEngine — validate → execute → fix → retry for Python scripts.

Mirrors CodingEngine's plan-generate-validate-fix loop but for data
processing scripts executed via ``lucy_run_script``.  Provides:

1. Syntax validation via ``py_compile`` before execution.
2. Runtime error recovery: on failure, feeds stderr back to a code-fix
   LLM call and retries up to ``MAX_FIX_ATTEMPTS`` times.
3. All attempts are logged for observability.

This ensures *any* LLM-generated script — regardless of integration or
data source — gets automatic validation and self-healing, matching the
quality guarantees that CodingEngine provides for Spaces apps.
"""

from __future__ import annotations

import asyncio
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import structlog

from lucy.config import settings
from lucy.core.openclaw import ChatConfig, get_openclaw_client

logger = structlog.get_logger()

MAX_FIX_ATTEMPTS = 3
_UNLIMITED_TOKENS = 100_000


async def _syntax_check(code: str) -> tuple[bool, str]:
    """Run py_compile on *code* and return (ok, error_message)."""
    tmp = Path(tempfile.mktemp(suffix=".py"))
    try:
        tmp.write_text(code, encoding="utf-8")
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "py_compile", str(tmp),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        err_text = (stderr or b"").decode("utf-8", errors="replace")
        if proc.returncode == 0:
            return True, ""
        return False, err_text.strip()[:1500]
    except asyncio.TimeoutError:
        return False, "Syntax check timed out (15s)"
    except Exception as e:
        return False, f"Syntax check crashed: {e}"
    finally:
        tmp.unlink(missing_ok=True)


_LARGE_SCRIPT_THRESHOLD = 8000


async def _ask_llm_to_fix(
    script: str,
    error_text: str,
    error_type: str,
) -> str:
    """Ask a code-fix model to correct the script given the error."""
    client = await get_openclaw_client()

    fix_prompt = (
        f"Fix the following Python script. It has a {error_type}.\n\n"
        f"ERROR:\n{error_text}\n\n"
        f"SCRIPT:\n```python\n{script}\n```\n\n"
        "Return ONLY the corrected full Python script, no explanations, "
        "no markdown fences. The script must be self-contained."
    )

    fix_model = (
        settings.model_tier_default
        if len(script) > _LARGE_SCRIPT_THRESHOLD
        else settings.model_tier_code
    )

    config = ChatConfig(
        model=fix_model,
        system_prompt=(
            "You are a Python code fixer. Return only the corrected "
            "script. No explanations, no markdown."
        ),
        max_tokens=_UNLIMITED_TOKENS,
        temperature=0.2,
    )

    try:
        response = await asyncio.wait_for(
            client.chat_completion(
                messages=[{"role": "user", "content": fix_prompt}],
                config=config,
            ),
            timeout=120,
        )
        fixed = response.content or ""
        fixed = _strip_fences(fixed)
        if len(fixed) < 30:
            logger.warning(
                "script_fix_too_short",
                fixed_length=len(fixed),
                error_type=error_type,
            )
            return script
        return fixed
    except asyncio.TimeoutError:
        logger.error(
            "script_fix_llm_timeout",
            error_type=error_type,
            model=fix_model,
        )
        return script
    except Exception as e:
        logger.error("script_fix_llm_failed", error=str(e))
        return script


def _strip_fences(code: str) -> str:
    """Remove markdown code fences that LLMs sometimes add."""
    stripped = code.strip()
    if stripped.startswith("```"):
        lines = stripped.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines)
    return stripped


async def execute_with_retry(
    script: str,
    workspace_id: str,
    timeout: int = 120,
) -> dict[str, Any]:
    """Validate, execute, and auto-fix a Python script.

    Returns a dict with keys: success, exit_code, stdout, stderr,
    elapsed_ms, attempts, fixes_applied.
    """
    from lucy.tools.script_tools import _execute_script_locally

    current_script = script
    fixes_applied: list[str] = []
    t0 = time.monotonic()

    for attempt in range(1, MAX_FIX_ATTEMPTS + 1):
        # Phase 1: Syntax check
        syntax_ok, syntax_err = await _syntax_check(current_script)
        if not syntax_ok:
            logger.warning(
                "script_syntax_error",
                attempt=attempt,
                error_preview=syntax_err[:300],
            )
            if attempt < MAX_FIX_ATTEMPTS:
                current_script = await _ask_llm_to_fix(
                    current_script, syntax_err, "SyntaxError",
                )
                fixes_applied.append(f"syntax_fix_attempt_{attempt}")
                continue
            return {
                "success": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Script has unfixable syntax errors after "
                          f"{MAX_FIX_ATTEMPTS} attempts:\n{syntax_err}",
                "elapsed_ms": round((time.monotonic() - t0) * 1000),
                "attempts": attempt,
                "fixes_applied": fixes_applied,
            }

        # Phase 2: Execute
        result = await _execute_script_locally(
            current_script, workspace_id, timeout,
        )

        stdout = result.output or ""
        stderr = result.error or ""

        if result.exit_code == 0:
            logger.info(
                "script_execution_success",
                attempt=attempt,
                fixes_applied=len(fixes_applied),
            )
            return {
                "success": True,
                "exit_code": 0,
                "stdout": stdout,
                "stderr": stderr,
                "elapsed_ms": round((time.monotonic() - t0) * 1000),
                "attempts": attempt,
                "fixes_applied": fixes_applied,
            }

        # Phase 3: Runtime error — try to fix
        error_tail = stderr.strip().split("\n")[-3:] if stderr else []
        logger.warning(
            "script_runtime_error",
            attempt=attempt,
            exit_code=result.exit_code,
            stderr_preview=stderr[:300],
            error_lines="\n".join(error_tail),
        )

        if attempt < MAX_FIX_ATTEMPTS:
            error_context = stderr if stderr else stdout
            if len(error_context) > 2000:
                error_context = error_context[-2000:]
            current_script = await _ask_llm_to_fix(
                current_script, error_context, "RuntimeError",
            )
            fixes_applied.append(f"runtime_fix_attempt_{attempt}")
            continue

        return {
            "success": False,
            "exit_code": result.exit_code,
            "stdout": stdout[:4000],
            "stderr": stderr[:2000],
            "elapsed_ms": round((time.monotonic() - t0) * 1000),
            "attempts": attempt,
            "fixes_applied": fixes_applied,
        }

    return {
        "success": False,
        "exit_code": -1,
        "stdout": "",
        "stderr": "Max fix attempts exhausted",
        "elapsed_ms": round((time.monotonic() - t0) * 1000),
        "attempts": MAX_FIX_ATTEMPTS,
        "fixes_applied": fixes_applied,
    }
