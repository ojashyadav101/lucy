"""Internal tool: code execution with pre-execution validation and auto-fix — V3.

Wraps the existing executor.py infrastructure as lucy_* internal tools.
Every Python code execution goes through a validation → fix → execute → analyze
pipeline that catches the vast majority of LLM code errors before they waste
execution time.

V3 changes (Sprint 2):
- Multi-language support: Python, JavaScript (Node.js), shell scripts
- Language auto-detection from code content
- Enhanced error recovery: TimeoutError → retry with hints, ImportError → pip install
- Human-readable error messages ("The code failed because X. Here's what I'll try next...")
- Smart output truncation (>5000 chars → head + tail + summary)
- Code review mode: static analysis without execution via review_code()
- Expanded dangerous-code checks for JS and shell

Architecture:
    lucy_execute_python  → validate → auto-fix? → execute → auto-install? → retry? → analyze
    lucy_execute_js      → validate → execute → retry? → analyze
    lucy_execute_bash    → execute (no validation — bash is too dynamic)
    lucy_run_script      → validate → execute
    review_code()        → static pattern analysis (no execution)

Pipeline for Python:
    1. Pre-validate (syntax, scope, imports) via code_validator
    2. If fixable issues found, auto-fix (add missing imports)
    3. Execute in LOCAL sandbox (subprocess)
    4. If ModuleNotFoundError, auto-install package and retry
    5. If TimeoutError, retry with reduced timeout hint
    6. If execution fails, analyze error and return structured hint
    7. If auto-retriable (max 2), fix and retry automatically
    8. Return formatted result with hints + file outputs
"""

from __future__ import annotations

import re
import shutil
import time
from typing import Any

import structlog

from lucy.tools.code_validator import (
    ValidationResult,
    analyze_execution_error,
    validate_python,
)
from lucy.workspace.executor import (
    ExecutionResult,
    auto_install_package,
    execute_bash,
    execute_python,
    execute_workspace_script,
    MAX_OUTPUT_CHARS,
)

logger = structlog.get_logger()


# ═══════════════════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════════════════

_MAX_RESULT_CHARS = 5000  # Truncate output for LLM context window (raised from 4000)
_MAX_TIMEOUT = 300
_MAX_AUTO_RETRIES = 2  # Max automatic retry attempts after failure
_MAX_AUTO_INSTALLS = 3  # Max package auto-installs per execution

# Output truncation thresholds
_OUTPUT_TRUNCATE_THRESHOLD = 5000  # Start truncation above this
_OUTPUT_HEAD_CHARS = 2000  # Keep first N chars
_OUTPUT_TAIL_CHARS = 1500  # Keep last N chars


# ═══════════════════════════════════════════════════════════════════════════
# LANGUAGE DETECTION
# ═══════════════════════════════════════════════════════════════════════════

def detect_language(code: str) -> str:
    """Auto-detect the programming language from code content.

    Returns one of: 'python', 'javascript', 'shell', 'unknown'.
    Uses a scoring heuristic based on syntax markers.
    """
    code_stripped = code.strip()
    scores: dict[str, int] = {"python": 0, "javascript": 0, "shell": 0}

    # ── Definitive markers (high confidence) ──
    # Shebang lines
    if code_stripped.startswith("#!/bin/bash") or code_stripped.startswith("#!/bin/sh"):
        return "shell"
    if code_stripped.startswith("#!/usr/bin/env python"):
        return "python"
    if code_stripped.startswith("#!/usr/bin/env node"):
        return "javascript"

    # ── Python markers ──
    py_patterns = [
        (r'\bdef\s+\w+\s*\(', 3),          # def function_name(
        (r'\bimport\s+\w+', 3),              # import module
        (r'\bfrom\s+\w+\s+import\b', 4),     # from module import
        (r'\bclass\s+\w+.*:', 3),             # class Foo:
        (r'\bprint\s*\(', 2),                 # print(
        (r'\bif\s+.*:\s*$', 2),               # if condition:
        (r'\belif\b', 3),                      # elif (Python-only)
        (r'\bself\.\w+', 3),                   # self.attr
        (r'"""', 2),                           # triple-quote docstring
        (r'\basync\s+def\b', 3),               # async def
        (r'\bawait\s+', 1),                    # await (also JS)
        (r'\bNone\b', 2),                      # None (Python-only)
        (r'\bTrue\b|\bFalse\b', 2),           # True/False
        (r'^\s*@\w+', 2),                     # decorators
    ]
    for pattern, weight in py_patterns:
        if re.search(pattern, code, re.MULTILINE):
            scores["python"] += weight

    # ── JavaScript markers ──
    js_patterns = [
        (r'\bconst\s+\w+\s*=', 3),            # const x =
        (r'\blet\s+\w+\s*=', 3),              # let x =
        (r'\bvar\s+\w+\s*=', 2),              # var x =
        (r'\bfunction\s+\w+\s*\(', 3),        # function name(
        (r'=>', 2),                             # arrow function
        (r'\bconsole\.\w+\(', 3),              # console.log(
        (r'\brequire\s*\(', 3),                # require(
        (r'\bmodule\.exports\b', 4),           # module.exports
        (r'\bexport\s+(default|const|function)\b', 4),  # ES modules
        (r'\bnull\b', 1),                      # null (also other langs)
        (r'\bundefined\b', 3),                 # undefined (JS-only)
        (r'\b===\b|\b!==\b', 3),              # strict equality
        (r'\bnew\s+Promise\b', 3),             # new Promise
        (r'\.then\s*\(', 2),                   # .then(
        (r'\.catch\s*\(', 1),                  # .catch(
    ]
    for pattern, weight in js_patterns:
        if re.search(pattern, code, re.MULTILINE):
            scores["javascript"] += weight

    # ── Shell markers ──
    sh_patterns = [
        (r'^\s*echo\s+', 2),                  # echo
        (r'\bfi\b', 3),                        # fi (shell-only)
        (r'\bdone\b', 2),                      # done (shell loop)
        (r'\bthen\b', 2),                      # then (shell if)
        (r'\besac\b', 4),                      # esac (shell-only)
        (r'\$\{?\w+\}?', 2),                  # $VAR or ${VAR}
        (r'\|\s*\w+', 1),                      # piped commands
        (r'^\s*if\s+\[', 3),                  # if [ condition ]
        (r'^\s*for\s+\w+\s+in\b', 3),         # for x in
        (r'^\s*while\s+\[', 3),               # while [
        (r'\bgrep\b|\bawk\b|\bsed\b', 3),     # common tools
        (r'\bcurl\b|\bwget\b', 2),             # download tools
        (r'&&\s*\w+', 1),                      # chained commands
    ]
    for pattern, weight in sh_patterns:
        if re.search(pattern, code, re.MULTILINE):
            scores["shell"] += weight

    # Pick the highest-scoring language (minimum threshold of 3)
    best = max(scores, key=scores.get)
    if scores[best] >= 3:
        return best

    return "unknown"


# ═══════════════════════════════════════════════════════════════════════════
# HUMAN-READABLE ERROR MESSAGES
# ═══════════════════════════════════════════════════════════════════════════

def _humanize_error(error_text: str, language: str = "python") -> str:
    """Convert raw tracebacks/errors into human-readable explanations.

    Returns a friendly message: "The code failed because X. Here's what I'll try next..."
    """
    error_lower = error_text.lower()

    # ── Python errors ──
    if language == "python":
        if "modulenotfounderror" in error_lower or "no module named" in error_lower:
            match = re.search(r"no module named ['\"]?([\w.]+)['\"]?", error_text, re.I)
            pkg = match.group(1) if match else "a package"
            return (
                f"The code failed because the package '{pkg}' is not installed. "
                f"I'll try to install it automatically and re-run."
            )

        if "syntaxerror" in error_lower:
            match = re.search(r"line (\d+)", error_text)
            line = f" on line {match.group(1)}" if match else ""
            return (
                f"The code has a syntax error{line}. "
                f"I'll check the code structure and fix the issue."
            )

        if "nameerror" in error_lower:
            match = re.search(r"name '(\w+)' is not defined", error_text)
            var = match.group(1) if match else "a variable"
            return (
                f"The code failed because '{var}' is not defined. "
                f"This usually means a missing import or variable declaration. "
                f"I'll try adding the missing import and retry."
            )

        if "typeerror" in error_lower:
            return (
                "The code hit a type error, meaning a value was used in a way "
                "its type doesn't support (e.g., adding a string to a number). "
                "I'll analyze the types and fix the operation."
            )

        if "keyerror" in error_lower:
            match = re.search(r"KeyError:\s*['\"]?(\w+)['\"]?", error_text)
            key = match.group(1) if match else "a key"
            return (
                f"The code tried to access key '{key}' in a dictionary, "
                f"but it doesn't exist. I'll add a check or use .get() with a default."
            )

        if "indexerror" in error_lower:
            return (
                "The code tried to access a list index that's out of range, "
                "meaning the list is shorter than expected. "
                "I'll add bounds checking and retry."
            )

        if "timeout" in error_lower or "timed out" in error_lower:
            return (
                "The code took too long to run and was stopped. "
                "I'll try optimizing it or breaking it into smaller steps."
            )

        if "permission" in error_lower or "denied" in error_lower:
            return (
                "The code was blocked by a permission error. "
                "The sandbox doesn't allow that operation. "
                "I'll find an alternative approach."
            )

        if "connectionerror" in error_lower or "urlopen" in error_lower:
            return (
                "The code couldn't connect to an external service. "
                "This could be a network issue or the URL might be wrong. "
                "I'll verify the URL and try again."
            )

        if "filenotfounderror" in error_lower:
            match = re.search(r"No such file.*?['\"](.+?)['\"]", error_text)
            path = match.group(1) if match else "the file"
            return (
                f"The code couldn't find '{path}'. "
                f"The file doesn't exist at that path. "
                f"I'll check the correct path and retry."
            )

    # ── JavaScript errors ──
    elif language == "javascript":
        if "syntaxerror" in error_lower:
            return (
                "The JavaScript code has a syntax error. "
                "I'll fix the syntax and re-run."
            )
        if "referenceerror" in error_lower:
            match = re.search(r"(\w+) is not defined", error_text)
            var = match.group(1) if match else "A variable"
            return (
                f"'{var}' is not defined in the JavaScript code. "
                f"I'll add the missing declaration or import and retry."
            )
        if "cannot find module" in error_lower:
            match = re.search(r"Cannot find module ['\"](.+?)['\"]", error_text)
            mod = match.group(1) if match else "a module"
            return (
                f"The Node.js module '{mod}' is not installed. "
                f"I'll install it with npm and re-run."
            )

    # ── Shell errors ──
    elif language == "shell":
        if "command not found" in error_lower:
            match = re.search(r"(\S+): command not found", error_text)
            cmd = match.group(1) if match else "A command"
            return (
                f"The command '{cmd}' is not available in this environment. "
                f"I'll find an alternative approach."
            )
        if "permission denied" in error_lower:
            return (
                "The shell command was blocked by permissions. "
                "I'll try a different approach that doesn't need elevated access."
            )

    # ── Fallback ──
    # Extract last meaningful line from traceback
    lines = [l.strip() for l in error_text.strip().splitlines() if l.strip()]
    last_line = lines[-1] if lines else error_text[:200]
    return (
        f"The code failed with: {last_line[:200]}. "
        f"I'll analyze the error and try a fix."
    )


# ═══════════════════════════════════════════════════════════════════════════
# OUTPUT TRUNCATION
# ═══════════════════════════════════════════════════════════════════════════

def _truncate_output(output: str) -> str:
    """Smart truncation: keep head + tail with a summary in the middle.

    If output is under _OUTPUT_TRUNCATE_THRESHOLD, return as-is.
    Otherwise, return head + middle summary + tail.
    """
    if len(output) <= _OUTPUT_TRUNCATE_THRESHOLD:
        return output

    total_chars = len(output)
    total_lines = output.count("\n") + 1
    head = output[:_OUTPUT_HEAD_CHARS]
    tail = output[-_OUTPUT_TAIL_CHARS:]

    # Count omitted content
    omitted_chars = total_chars - _OUTPUT_HEAD_CHARS - _OUTPUT_TAIL_CHARS
    omitted_start_line = head.count("\n") + 1
    omitted_end_line = total_lines - tail.count("\n")

    summary = (
        f"\n\n... [{omitted_chars:,} chars, ~lines {omitted_start_line}-{omitted_end_line} "
        f"omitted — {total_chars:,} total chars, {total_lines} total lines] ...\n\n"
    )

    return head + summary + tail


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
                    "run analysis, generate outputs, or test code.\n\n"
                    "IMPORTANT: Each execution is INDEPENDENT — no shared state "
                    "between calls. All variables, imports, and data must be "
                    "defined within the SAME code block. Do NOT reference "
                    "variables from previous executions.\n\n"
                    "Available packages: requests, httpx, json, csv, re, math, "
                    "datetime, collections, itertools, pathlib, os, sys, "
                    "hashlib, base64, uuid, statistics, sqlite3, pandas, numpy, "
                    "matplotlib, beautifulsoup4, pyyaml, tabulate, "
                    "and more standard library modules. "
                    "Missing packages are auto-installed on first use.\n\n"
                    "Use print() for all output — only stdout is captured.\n\n"
                    "PREFER THIS over COMPOSIO_REMOTE_WORKBENCH for faster execution."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": (
                                "Self-contained Python code. Must include all "
                                "imports and variable definitions. Use print() "
                                "for output."
                            ),
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
                "name": "lucy_execute_js",
                "description": (
                    "Execute JavaScript code using Node.js in a sandboxed environment. "
                    "Use this when the task specifically requires JavaScript/Node.js, "
                    "e.g., testing JS snippets, running npm scripts, or working with "
                    "Node.js APIs.\n\n"
                    "IMPORTANT: Each execution is INDEPENDENT. Use console.log() for output.\n\n"
                    "Node.js built-ins are available (fs, path, http, crypto, etc.). "
                    "Missing npm packages are auto-installed on first use."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": (
                                "Self-contained JavaScript code for Node.js. "
                                "Use console.log() for output."
                            ),
                        },
                        "description": {
                            "type": "string",
                            "description": "Brief description of what this code does.",
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
                    "system info, package installation, or piped commands.\n\n"
                    "PREFER THIS over COMPOSIO_REMOTE_BASH_TOOL for faster execution."
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


def is_code_tool(tool_name: str) -> bool:
    """Check if a tool name is a code execution tool."""
    return tool_name in {
        "lucy_execute_python",
        "lucy_execute_js",
        "lucy_execute_bash",
        "lucy_run_script",
    }


# ═══════════════════════════════════════════════════════════════════════════
# TOOL EXECUTION — main entry point
# ═══════════════════════════════════════════════════════════════════════════

async def execute_code_tool(
    tool_name: str,
    parameters: dict[str, Any],
    workspace_id: str = "",
) -> dict[str, Any]:
    """Execute a code tool with validation, auto-fix, and error analysis.

    Pipeline:
    1. Validate code (Python only)
    2. Auto-fix if possible
    3. Execute
    4. On failure: auto-install missing packages, analyze error, retry
    5. Return formatted result with hints
    """
    timeout = min(parameters.get("timeout", 60), _MAX_TIMEOUT)
    description = parameters.get("description", "")

    t0 = time.monotonic()

    try:
        if tool_name == "lucy_execute_python":
            return await _execute_python_with_validation(
                parameters, workspace_id, timeout, description, t0,
            )

        elif tool_name == "lucy_execute_js":
            return await _execute_js_tool(
                parameters, workspace_id, timeout, description, t0,
            )

        elif tool_name == "lucy_execute_bash":
            return await _execute_bash_tool(
                parameters, workspace_id, timeout, description, t0,
            )

        elif tool_name == "lucy_run_script":
            return await _execute_script_tool(
                parameters, workspace_id, timeout, description, t0,
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
        return {
            "error": f"Execution failed: {str(e)[:200]}",
            "error_explanation": _humanize_error(str(e), _tool_to_language(tool_name)),
        }


def _tool_to_language(tool_name: str) -> str:
    """Map tool name to language string."""
    return {
        "lucy_execute_python": "python",
        "lucy_execute_js": "javascript",
        "lucy_execute_bash": "shell",
        "lucy_run_script": "python",
    }.get(tool_name, "python")


# ═══════════════════════════════════════════════════════════════════════════
# PYTHON EXECUTION — with validation pipeline
# ═══════════════════════════════════════════════════════════════════════════

async def _execute_python_with_validation(
    parameters: dict[str, Any],
    workspace_id: str,
    timeout: int,
    description: str,
    t0: float,
) -> dict[str, Any]:
    """Execute Python code with pre-validation, auto-fix, auto-install, and error analysis."""
    code = parameters.get("code", "")
    if not code.strip():
        return {"error": "No code provided."}

    # ── Security check ──
    danger = _check_dangerous_code(code, language="python")
    if danger:
        return {"error": f"Blocked: {danger}"}

    # ── Step 1: Pre-execution validation ──
    validation = validate_python(code, auto_fix=True)

    if not validation.valid and not validation.fixed_code:
        # Code has unfixable errors — don't waste an execution attempt
        elapsed_ms = round((time.monotonic() - t0) * 1000)
        logger.info(
            "code_validation_rejected",
            issues=len(validation.issues),
            elapsed_ms=elapsed_ms,
            description=description,
        )
        return {
            "success": False,
            "execution_method": "pre_validation",
            "elapsed_ms": elapsed_ms,
            "error": validation.format_for_llm(),
            "error_explanation": (
                "The code has issues that need to be fixed before it can run. "
                "Check the error details above and correct the problems."
            ),
            "validation_failed": True,
            "hint": (
                "Fix the issues above and try again. Remember: each execution "
                "is a fresh, independent environment with no shared state."
            ),
        }

    # ── Step 2: Use fixed code if available ──
    execute_code = validation.fixed_code or code
    was_auto_fixed = validation.fixed_code is not None

    if was_auto_fixed:
        logger.info(
            "code_auto_fixed_before_execution",
            original_issues=len(validation.issues),
            description=description,
        )

    # ── Step 3: Execute (with retry loop including auto-install) ──
    retries = 0
    auto_installs = 0
    last_result: ExecutionResult | None = None
    last_error_hint = ""
    timeout_retried = False

    while retries <= _MAX_AUTO_RETRIES:
        result = await execute_python(
            workspace_id=workspace_id,
            code=execute_code,
            timeout=timeout,
        )

        if result.success:
            elapsed_ms = round((time.monotonic() - t0) * 1000)
            formatted = _format_result(result, elapsed_ms, description)
            if was_auto_fixed:
                formatted["note"] = (
                    "Code was auto-fixed before execution (added missing imports)."
                )
            if result.files_created:
                formatted["files_created"] = result.files_created
                formatted["note"] = (
                    (formatted.get("note", "") + " " if formatted.get("note") else "")
                    + f"Created {len(result.files_created)} file(s): "
                    + ", ".join(result.files_created[:5])
                )

            await _log_execution(
                workspace_id, "lucy_execute_python", description, result, elapsed_ms,
            )
            logger.info(
                "code_tool_executed",
                tool="lucy_execute_python",
                success=True,
                method=result.method,
                elapsed_ms=elapsed_ms,
                retries=retries,
                auto_fixed=was_auto_fixed,
                auto_installs=auto_installs,
                description=description,
            )
            return formatted

        # ── Execution failed — structured recovery ──
        last_result = result
        error_text = result.error or f"Exit code: {result.exit_code}"

        # Recovery 1: Auto-install for ModuleNotFoundError
        if auto_installs < _MAX_AUTO_INSTALLS:
            package = _extract_missing_package(error_text)
            if package:
                installed = await auto_install_package(package)
                if installed:
                    auto_installs += 1
                    logger.info(
                        "code_auto_install_retry",
                        package=package,
                        install_count=auto_installs,
                        description=description,
                    )
                    continue  # Retry with same code after installing

        # Recovery 2: Timeout → retry once with hint
        if not timeout_retried and _is_timeout_error(error_text):
            timeout_retried = True
            retries += 1
            # Don't change the code, but increase timeout
            timeout = min(timeout * 2, _MAX_TIMEOUT)
            logger.info(
                "code_timeout_retry",
                new_timeout=timeout,
                retry=retries,
                description=description,
            )
            continue

        # Recovery 3: Try auto-fix from error analysis
        last_error_hint = analyze_execution_error(error_text, execute_code)
        retry_fix = _try_auto_fix_from_error(execute_code, error_text)
        if retry_fix and retries < _MAX_AUTO_RETRIES:
            execute_code = retry_fix
            retries += 1
            logger.info(
                "code_auto_retry",
                retry=retries,
                error_preview=error_text[:100],
                description=description,
            )
            continue

        # Not retriable — break out
        break

    # ── All attempts failed — return structured error with human explanation ──
    elapsed_ms = round((time.monotonic() - t0) * 1000)
    assert last_result is not None

    formatted = _format_result(last_result, elapsed_ms, description)
    formatted["error_analysis"] = last_error_hint
    formatted["error_explanation"] = _humanize_error(
        last_result.error or f"Exit code: {last_result.exit_code}",
        "python",
    )
    if retries > 0 or auto_installs > 0:
        parts = []
        if retries > 0:
            parts.append(f"{retries} automatic fix(es)")
        if auto_installs > 0:
            parts.append(f"{auto_installs} package install(s)")
        formatted["auto_retries"] = retries
        formatted["auto_installs"] = auto_installs
        formatted["note"] = (
            f"Attempted {' and '.join(parts)}, but the error persists. "
            f"See error_explanation for a plain-English summary."
        )

    await _log_execution(
        workspace_id, "lucy_execute_python", description, last_result, elapsed_ms,
    )
    logger.info(
        "code_tool_executed",
        tool="lucy_execute_python",
        success=False,
        method=last_result.method,
        elapsed_ms=elapsed_ms,
        retries=retries,
        auto_installs=auto_installs,
        error_preview=last_result.error[:100] if last_result.error else "",
        description=description,
    )
    return formatted


# ═══════════════════════════════════════════════════════════════════════════
# JAVASCRIPT EXECUTION — Node.js
# ═══════════════════════════════════════════════════════════════════════════

async def _execute_js_tool(
    parameters: dict[str, Any],
    workspace_id: str,
    timeout: int,
    description: str,
    t0: float,
) -> dict[str, Any]:
    """Execute JavaScript code via Node.js with error recovery."""
    code = parameters.get("code", "")
    if not code.strip():
        return {"error": "No code provided."}

    # ── Security check ──
    danger = _check_dangerous_code(code, language="javascript")
    if danger:
        return {"error": f"Blocked: {danger}"}

    # ── Check Node.js availability ──
    if not shutil.which("node"):
        return {
            "error": "Node.js is not available in this environment.",
            "error_explanation": (
                "Node.js isn't installed in the sandbox. "
                "Try using Python instead, or install Node.js first with a bash command."
            ),
            "hint": "Use lucy_execute_python for a similar task, or install node first.",
        }

    retries = 0
    auto_installs = 0
    last_result: ExecutionResult | None = None

    while retries <= _MAX_AUTO_RETRIES:
        # Execute JS via bash (node -e or temp file for multi-line)
        if "\n" in code.strip() or len(code) > 200:
            # Multi-line: write to temp file and run
            escaped_code = code.replace("'", "'\\''")
            command = f"cat << 'JSEOF' > /tmp/_lucy_exec.js\n{code}\nJSEOF\nnode /tmp/_lucy_exec.js"
        else:
            escaped_code = code.replace("'", "'\\''")
            command = f"node -e '{escaped_code}'"

        result = await execute_bash(
            workspace_id=workspace_id,
            command=command,
            timeout=timeout,
        )

        if result.success:
            elapsed_ms = round((time.monotonic() - t0) * 1000)
            formatted = _format_result(result, elapsed_ms, description)
            await _log_execution(
                workspace_id, "lucy_execute_js", description, result, elapsed_ms,
            )
            logger.info(
                "code_tool_executed",
                tool="lucy_execute_js",
                success=True,
                method="node",
                elapsed_ms=elapsed_ms,
                retries=retries,
                auto_installs=auto_installs,
                description=description,
            )
            return formatted

        last_result = result
        error_text = result.error or f"Exit code: {result.exit_code}"

        # Recovery: npm install for missing modules
        if auto_installs < _MAX_AUTO_INSTALLS:
            npm_pkg = _extract_missing_npm_package(error_text)
            if npm_pkg:
                install_result = await execute_bash(
                    workspace_id=workspace_id,
                    command=f"npm install --no-save {npm_pkg} 2>&1",
                    timeout=60,
                )
                if install_result.success:
                    auto_installs += 1
                    logger.info(
                        "js_auto_install_retry",
                        package=npm_pkg,
                        install_count=auto_installs,
                        description=description,
                    )
                    continue

        retries += 1
        if retries > _MAX_AUTO_RETRIES:
            break

    # ── Failed ──
    elapsed_ms = round((time.monotonic() - t0) * 1000)
    assert last_result is not None
    formatted = _format_result(last_result, elapsed_ms, description)
    formatted["error_explanation"] = _humanize_error(
        last_result.error or f"Exit code: {last_result.exit_code}",
        "javascript",
    )
    await _log_execution(
        workspace_id, "lucy_execute_js", description, last_result, elapsed_ms,
    )
    return formatted


# ═══════════════════════════════════════════════════════════════════════════
# BASH EXECUTION — no validation (bash is too dynamic)
# ═══════════════════════════════════════════════════════════════════════════

async def _execute_bash_tool(
    parameters: dict[str, Any],
    workspace_id: str,
    timeout: int,
    description: str,
    t0: float,
) -> dict[str, Any]:
    """Execute bash command with safety checks and human-readable errors."""
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

    elapsed_ms = round((time.monotonic() - t0) * 1000)
    formatted = _format_result(result, elapsed_ms, description)

    if not result.success:
        formatted["error_explanation"] = _humanize_error(
            result.error or f"Exit code: {result.exit_code}",
            "shell",
        )

    await _log_execution(
        workspace_id, "lucy_execute_bash", description, result, elapsed_ms,
    )
    logger.info(
        "code_tool_executed",
        tool="lucy_execute_bash",
        success=result.success,
        method=result.method,
        elapsed_ms=elapsed_ms,
        description=description,
    )
    return formatted


# ═══════════════════════════════════════════════════════════════════════════
# SCRIPT EXECUTION — validated before run
# ═══════════════════════════════════════════════════════════════════════════

async def _execute_script_tool(
    parameters: dict[str, Any],
    workspace_id: str,
    timeout: int,
    description: str,
    t0: float,
) -> dict[str, Any]:
    """Execute a workspace script with pre-validation."""
    script_path = parameters.get("script_path", "")
    if not script_path:
        return {"error": "No script_path provided."}

    args = parameters.get("args", [])

    # Pre-validate the script if we can read it
    try:
        from lucy.workspace.filesystem import get_workspace
        ws = get_workspace(workspace_id)
        code = await ws.read_file(script_path)
        if code:
            validation = validate_python(code, auto_fix=False)
            if not validation.valid:
                elapsed_ms = round((time.monotonic() - t0) * 1000)
                return {
                    "success": False,
                    "execution_method": "pre_validation",
                    "elapsed_ms": elapsed_ms,
                    "error": validation.format_for_llm(),
                    "error_explanation": (
                        f"The script '{script_path}' has validation errors that "
                        f"must be fixed before it can run."
                    ),
                    "validation_failed": True,
                    "hint": f"Script '{script_path}' has validation errors. Fix and re-save.",
                }
    except Exception:
        pass  # If we can't read it, let execute_workspace_script handle the error

    result = await execute_workspace_script(
        workspace_id=workspace_id,
        script_path=script_path,
        args=args,
        timeout=timeout,
    )

    elapsed_ms = round((time.monotonic() - t0) * 1000)
    formatted = _format_result(result, elapsed_ms, description)

    if not result.success:
        error_text = result.error or f"Exit code: {result.exit_code}"
        formatted["error_analysis"] = analyze_execution_error(error_text, "")
        formatted["error_explanation"] = _humanize_error(error_text, "python")

    await _log_execution(
        workspace_id, "lucy_run_script", description, result, elapsed_ms,
    )
    logger.info(
        "code_tool_executed",
        tool="lucy_run_script",
        success=result.success,
        method=result.method,
        elapsed_ms=elapsed_ms,
        description=description,
    )
    return formatted


# ═══════════════════════════════════════════════════════════════════════════
# CODE REVIEW MODE — static analysis without execution
# ═══════════════════════════════════════════════════════════════════════════

def review_code(code: str, language: str = "") -> str:
    """Analyze code without executing it. Returns structured feedback.

    Uses pattern matching (not LLM) to detect common anti-patterns,
    security concerns, and style issues.

    Args:
        code: The source code to review.
        language: Language hint ('python', 'javascript', 'shell').
                  Auto-detected if empty.

    Returns:
        Structured feedback string with issues, suggestions, and security notes.
    """
    if not language:
        language = detect_language(code)

    issues: list[dict[str, str]] = []       # severity: error, warning, info
    suggestions: list[str] = []
    security: list[str] = []

    if language == "python":
        _review_python(code, issues, suggestions, security)
    elif language == "javascript":
        _review_javascript(code, issues, suggestions, security)
    elif language == "shell":
        _review_shell(code, issues, suggestions, security)
    else:
        # Try all reviewers, report what we find
        _review_python(code, issues, suggestions, security)

    # ── Format output ──
    parts: list[str] = []
    parts.append(f"Code Review ({language or 'auto-detected'})")
    parts.append("=" * 40)

    if not issues and not suggestions and not security:
        parts.append("✅ No issues found. Code looks clean.")
        return "\n".join(parts)

    if security:
        parts.append("")
        parts.append("🔒 SECURITY CONCERNS:")
        for item in security:
            parts.append(f"  ⚠️  {item}")

    if issues:
        parts.append("")
        parts.append("🐛 ISSUES:")
        for item in issues:
            severity_icon = {"error": "❌", "warning": "⚠️", "info": "ℹ️"}.get(
                item["severity"], "•"
            )
            parts.append(f"  {severity_icon} [{item['severity'].upper()}] {item['message']}")

    if suggestions:
        parts.append("")
        parts.append("💡 SUGGESTIONS:")
        for item in suggestions:
            parts.append(f"  • {item}")

    parts.append("")
    parts.append(f"Total: {len(issues)} issue(s), {len(security)} security concern(s), {len(suggestions)} suggestion(s)")
    return "\n".join(parts)


def _review_python(
    code: str,
    issues: list[dict[str, str]],
    suggestions: list[str],
    security: list[str],
) -> None:
    """Review Python code for common anti-patterns."""
    lines = code.splitlines()

    # ── Security checks ──
    if re.search(r'\beval\s*\(', code):
        security.append("eval() usage detected. This can execute arbitrary code. Use ast.literal_eval() for safe parsing.")
    if re.search(r'\bexec\s*\(', code):
        security.append("exec() usage detected. Avoid executing dynamic code strings.")
    if re.search(r'\bos\.system\s*\(', code):
        security.append("os.system() detected. Use subprocess.run() with shell=False for safer command execution.")
    if re.search(r'\bpickle\.loads?\s*\(', code):
        security.append("pickle.load() detected. Deserializing untrusted data can execute arbitrary code.")
    if re.search(r'\bsubprocess\.\w+\(.*shell\s*=\s*True', code):
        security.append("subprocess with shell=True detected. This can be vulnerable to shell injection.")
    if re.search(r'password|secret|api_key|token', code, re.I) and re.search(r'["\'][\w]{8,}["\']', code):
        security.append("Possible hardcoded credential detected. Use environment variables instead.")

    # ── Error handling ──
    if re.search(r'\bexcept\s*:', code) and not re.search(r'\bexcept\s+\w+', code):
        issues.append({
            "severity": "warning",
            "message": "Bare 'except:' catches all exceptions including KeyboardInterrupt. Catch specific exceptions.",
        })
    if re.search(r'\bexcept\s*:\s*\n\s*pass\b', code):
        issues.append({
            "severity": "warning",
            "message": "Silent exception swallowing (except: pass). Errors should be logged or handled.",
        })

    # ── Common anti-patterns ──
    for i, line in enumerate(lines, 1):
        # Mutable default arguments
        if re.search(r'def\s+\w+\s*\(.*=\s*(\[\]|\{\})\s*[,)]', line):
            issues.append({
                "severity": "error",
                "message": f"Line {i}: Mutable default argument (list/dict). Use None and initialize inside the function.",
            })

        # String concatenation in loops
        if re.search(r'\w+\s*\+=\s*["\']', line) or re.search(r'\w+\s*=\s*\w+\s*\+\s*["\']', line):
            if any(re.search(r'\bfor\b', lines[j]) for j in range(max(0, i-5), i)):
                suggestions.append(
                    f"Line {i}: String concatenation in/near a loop. Consider using ''.join() or a list for better performance."
                )

    # ── Import checks ──
    if re.search(r'from\s+\w+\s+import\s+\*', code):
        issues.append({
            "severity": "warning",
            "message": "Wildcard import (from X import *) pollutes the namespace. Import specific names.",
        })

    # ── Style/quality suggestions ──
    if not re.search(r'""".*?"""|\'\'\'.*?\'\'\'', code, re.DOTALL) and len(lines) > 20:
        suggestions.append("Consider adding docstrings for functions/classes in longer scripts.")
    if not re.search(r'\btry\b', code) and (re.search(r'\bopen\s*\(', code) or re.search(r'\brequests?\.\w+\(', code)):
        suggestions.append("I/O operations (file/network) should have try/except error handling.")
    if re.search(r'print\s*\(.*\bpassword\b.*\)', code, re.I):
        security.append("Printing password or sensitive data to stdout.")

    # ── Resource management ──
    if re.search(r'open\s*\(', code) and not re.search(r'\bwith\s+open\b', code):
        issues.append({
            "severity": "warning",
            "message": "File opened without 'with' statement. Use 'with open(...)' to ensure proper cleanup.",
        })


def _review_javascript(
    code: str,
    issues: list[dict[str, str]],
    suggestions: list[str],
    security: list[str],
) -> None:
    """Review JavaScript code for common anti-patterns."""
    lines = code.splitlines()

    # ── Security checks ──
    if re.search(r'\beval\s*\(', code):
        security.append("eval() usage detected. This can execute arbitrary code. Use JSON.parse() for data or Function constructor carefully.")
    if re.search(r'innerHTML\s*=', code):
        security.append("innerHTML assignment detected. This can lead to XSS attacks. Use textContent or sanitize input.")
    if re.search(r'document\.write\s*\(', code):
        security.append("document.write() detected. This can overwrite the entire page and is a potential XSS vector.")
    if re.search(r'password|secret|api_key|token', code, re.I) and re.search(r'["\'][\w]{8,}["\']', code):
        security.append("Possible hardcoded credential detected. Use environment variables instead.")

    # ── Anti-patterns ──
    for i, line in enumerate(lines, 1):
        if re.search(r'\bvar\s+', line):
            issues.append({
                "severity": "info",
                "message": f"Line {i}: 'var' used. Prefer 'const' or 'let' for block scoping.",
            })
        if re.search(r'==(?!=)', line) and not re.search(r'===', line):
            issues.append({
                "severity": "warning",
                "message": f"Line {i}: Loose equality (==) used. Prefer strict equality (===).",
            })

    # ── Error handling ──
    if re.search(r'\.catch\s*\(\s*\)', code) or re.search(r'catch\s*\(\w+\)\s*\{\s*\}', code):
        issues.append({
            "severity": "warning",
            "message": "Empty catch block detected. Errors should be logged or handled.",
        })

    # ── Async patterns ──
    if re.search(r'\.then\s*\(', code) and re.search(r'\basync\b', code):
        suggestions.append("Mixing .then() chains with async/await. Consider using async/await consistently.")
    if re.search(r'\bawait\b', code) and not re.search(r'\btry\b', code):
        suggestions.append("Async operations without try/catch. Consider wrapping await calls in try/catch.")

    # ── Node.js specific ──
    if re.search(r'require\s*\(', code) and re.search(r'\bimport\s+', code):
        suggestions.append("Mixing require() and ES import syntax. Use one module system consistently.")


def _review_shell(
    code: str,
    issues: list[dict[str, str]],
    suggestions: list[str],
    security: list[str],
) -> None:
    """Review shell scripts for common anti-patterns."""
    lines = code.splitlines()

    # ── Security checks ──
    if re.search(r'\beval\b', code):
        security.append("eval usage in shell script. This can execute arbitrary commands.")
    if re.search(r'\bcurl\b.*\|\s*(ba)?sh\b', code):
        security.append("Piping curl output to shell. This is a major security risk with untrusted URLs.")
    if re.search(r'chmod\s+777\b', code):
        security.append("chmod 777 gives full permissions to everyone. Use more restrictive permissions.")

    # ── Best practices ──
    if not re.search(r'set\s+-[euo]\b|set\s+-\w*e', code):
        suggestions.append("Consider adding 'set -euo pipefail' for safer script execution (exit on errors, undefined vars, pipe failures).")

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        # Unquoted variables
        if re.search(r'\$\w+(?!\})', stripped) and not re.search(r'["\'].*\$\w+.*["\']', stripped):
            if not stripped.startswith("#"):
                issues.append({
                    "severity": "warning",
                    "message": f"Line {i}: Unquoted variable expansion. Use \"$VAR\" to prevent word splitting.",
                })
                break  # Only report once

    # ── Command safety ──
    if re.search(r'\brm\s+-rf\b', code) and not re.search(r'\brm\s+-rf\s+["\']?\$', code):
        issues.append({
            "severity": "warning",
            "message": "'rm -rf' with a hardcoded path. Consider using variables and confirming the path.",
        })


# ═══════════════════════════════════════════════════════════════════════════
# AUTO-RETRY FIXERS (post-execution)
# ═══════════════════════════════════════════════════════════════════════════

def _is_timeout_error(error_text: str) -> bool:
    """Check if the error is a timeout-related error."""
    timeout_markers = [
        "timeout", "timed out", "timedout", "time limit",
        "deadline exceeded", "execution expired",
        "killed", "signal 9",  # Process killed (often timeout)
    ]
    error_lower = error_text.lower()
    return any(marker in error_lower for marker in timeout_markers)


def _extract_missing_package(error_text: str) -> str | None:
    """Extract the missing package name from a ModuleNotFoundError.

    Returns the package name to install, or None.
    """
    if "modulenotfounderror" not in error_text.lower():
        return None

    match = re.search(r"no module named ['\"]?([\w.]+)['\"]?", error_text, re.IGNORECASE)
    if match:
        module_name = match.group(1).split(".")[0]  # Top-level module

        # Skip stdlib modules (they shouldn't be missing)
        _STDLIB = {
            "os", "sys", "json", "csv", "re", "math", "random", "time",
            "datetime", "collections", "itertools", "functools", "pathlib",
            "io", "typing", "dataclasses", "enum", "abc", "contextlib",
            "logging", "warnings", "traceback", "inspect", "unittest",
            "hashlib", "base64", "uuid", "secrets", "statistics",
            "sqlite3", "subprocess", "asyncio", "threading",
            "urllib", "http", "email", "html", "xml",
            "copy", "shutil", "tempfile", "glob", "struct",
            "argparse", "textwrap", "string", "operator",
            "heapq", "bisect", "array", "queue", "calendar",
            "gzip", "zipfile", "tarfile", "pickle",
        }
        if module_name in _STDLIB:
            return None

        # Common module → package name mappings
        _MODULE_TO_PACKAGE = {
            "cv2": "opencv-python",
            "PIL": "Pillow",
            "sklearn": "scikit-learn",
            "bs4": "beautifulsoup4",
            "yaml": "pyyaml",
            "dotenv": "python-dotenv",
            "gi": "PyGObject",
            "attr": "attrs",
            "dateutil": "python-dateutil",
            "jose": "python-jose",
            "jwt": "PyJWT",
            "lxml": "lxml",
        }

        return _MODULE_TO_PACKAGE.get(module_name, module_name)

    return None


def _extract_missing_npm_package(error_text: str) -> str | None:
    """Extract missing npm package name from a Node.js error."""
    match = re.search(r"Cannot find module ['\"](.+?)['\"]", error_text)
    if match:
        module_name = match.group(1)
        # Skip built-in modules
        if not module_name.startswith(".") and not module_name.startswith("/"):
            # Get the package name (handle scoped packages like @scope/pkg)
            if module_name.startswith("@"):
                parts = module_name.split("/")
                return "/".join(parts[:2]) if len(parts) >= 2 else module_name
            return module_name.split("/")[0]
    return None


def _try_auto_fix_from_error(code: str, error_text: str) -> str | None:
    """Try to fix code based on a runtime error.

    Only fixes unambiguous, safe issues:
    - Missing import (NameError for known modules)
    """
    import ast

    error_lower = error_text.lower()

    # ── NameError → add missing import ──
    if "nameerror" in error_lower:
        match = re.search(r"name '(\w+)' is not defined", error_text)
        if match:
            var_name = match.group(1)
            from lucy.tools.code_validator import _COMMON_MISSING_IMPORTS
            if var_name in _COMMON_MISSING_IMPORTS:
                import_line = _COMMON_MISSING_IMPORTS[var_name]
                if import_line not in code:
                    fixed = f"{import_line}\n{code}"
                    try:
                        ast.parse(fixed)
                        return fixed
                    except SyntaxError:
                        return None

    return None


# ═══════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _format_result(
    result: ExecutionResult,
    elapsed_ms: int,
    description: str,
) -> dict[str, Any]:
    """Format an ExecutionResult for the LLM, with smart output truncation."""
    output = result.output.strip()
    error = result.error.strip()

    # Smart truncation: keep head + tail with summary
    if len(output) > _OUTPUT_TRUNCATE_THRESHOLD:
        output = _truncate_output(output)
    elif len(output) > _MAX_RESULT_CHARS:
        output = (
            output[:_MAX_RESULT_CHARS]
            + f"\n... (truncated, {len(result.output)} total chars)"
        )

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


def _check_dangerous_code(code: str, language: str = "python") -> str | None:
    """Check code for obviously dangerous operations. Covers Python, JS, and shell."""
    code_lower = code.lower()

    # ── Universal dangerous patterns ──
    universal_patterns = [
        ("rm -rf /", "destructive remove-all command"),
        (":(){ :|:&};:", "fork bomb detected"),
    ]
    for pattern, reason in universal_patterns:
        if pattern in code_lower:
            return reason

    # ── Python-specific ──
    if language == "python":
        dangerous_patterns = [
            ("os.system('rm -rf", "destructive system command"),
            ("shutil.rmtree('/'", "destructive file operation"),
            ("subprocess.call(['rm'", "destructive subprocess"),
            ("os.remove('/'", "destructive file operation"),
            ("open('/etc/", "system file access"),
        ]
        for pattern, reason in dangerous_patterns:
            if pattern in code_lower:
                return reason

    # ── JavaScript-specific ──
    elif language == "javascript":
        js_patterns = [
            ("child_process.exec('rm -rf", "destructive system command"),
            ("fs.rmdir('/'", "destructive file operation"),
            ("fs.unlinkSync('/'", "destructive file operation"),
            ("require('child_process').exec('rm", "destructive subprocess"),
        ]
        for pattern, reason in js_patterns:
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
        "wget -O- | sh",
        "curl | sh",
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


# ═══════════════════════════════════════════════════════════════════════════
# CODE TEMPLATES — common patterns for the LLM to start from
# ═══════════════════════════════════════════════════════════════════════════

CODE_TEMPLATES: dict[str, str] = {
    "api_data_processing": '''\
"""Fetch and process data from an API."""
import json
import urllib.request
import urllib.parse

API_URL = "https://api.example.com/endpoint"
HEADERS = {{"Authorization": "Bearer YOUR_TOKEN"}}

def fetch_all(url, headers=None):
    all_data = []
    page = 1
    while True:
        page_url = f"{{url}}?page={{page}}&per_page=100"
        req = urllib.request.Request(page_url, headers=headers or {{}})
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
        if isinstance(data, list):
            if not data:
                break
            all_data.extend(data)
        elif isinstance(data, dict):
            items = data.get("results", data.get("data", data.get("items", [])))
            if not items:
                break
            all_data.extend(items)
            if not data.get("next"):
                break
        page += 1
    return all_data

results = fetch_all(API_URL, HEADERS)
print(f"Fetched {{len(results)}} items")
for item in results[:5]:
    print(json.dumps(item, indent=2))
''',

    "data_analysis": '''\
"""Load, transform, analyze, and format data."""
import json
import csv
import statistics
from collections import Counter, defaultdict
from io import StringIO

raw_data = """[
    {{"name": "Alice", "department": "Engineering", "score": 92}},
    {{"name": "Bob", "department": "Marketing", "score": 85}},
    {{"name": "Charlie", "department": "Engineering", "score": 78}}
]"""
data = json.loads(raw_data)

scores = [item["score"] for item in data]
by_dept = defaultdict(list)
for item in data:
    by_dept[item["department"]].append(item["score"])

print(f"Total records: {{len(data)}}")
print(f"Average score: {{statistics.mean(scores):.1f}}")
print(f"Median score: {{statistics.median(scores):.1f}}")

for dept, dept_scores in sorted(by_dept.items()):
    avg = statistics.mean(dept_scores)
    print(f"{{dept}}: {{len(dept_scores)}} people, avg={{avg:.1f}}")
''',

    "file_generation": '''\
"""Create, write, and verify a file."""
import json
import os

OUTPUT_PATH = "output.json"

content = {{
    "title": "Generated Report",
    "timestamp": __import__("datetime").datetime.now().isoformat(),
    "data": [
        {{"key": "metric_1", "value": 42}},
        {{"key": "metric_2", "value": 99}},
    ],
}}

with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
    json.dump(content, f, indent=2)

if os.path.isfile(OUTPUT_PATH):
    size = os.path.getsize(OUTPUT_PATH)
    print(f"✅ File written: {{OUTPUT_PATH}} ({{size}} bytes)")
''',

    "http_request": '''\
"""Make HTTP requests with error handling."""
import json
import urllib.request
import urllib.error

URL = "https://httpbin.org/get"
HEADERS = {{"User-Agent": "Lucy/1.0", "Accept": "application/json"}}

try:
    req = urllib.request.Request(URL, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        status = resp.status
        body = resp.read().decode("utf-8")
    print(f"Status: {{status}}")
    data = json.loads(body)
    print(json.dumps(data, indent=2))
except urllib.error.HTTPError as e:
    print(f"HTTP Error {{e.code}}: {{e.reason}}")
except urllib.error.URLError as e:
    print(f"Connection error: {{e.reason}}")
''',
}


def get_code_template(template_name: str) -> str | None:
    """Get a code template by name."""
    return CODE_TEMPLATES.get(template_name)


def list_code_templates() -> list[dict[str, str]]:
    """List available code templates."""
    descriptions = {
        "api_data_processing": "Fetch → paginate → process → output from an API",
        "data_analysis": "Load → transform → analyze → format data with statistics",
        "file_generation": "Create → write → verify a file",
        "http_request": "Make HTTP requests with proper error handling",
    }
    return [
        {"name": name, "description": descriptions.get(name, "")}
        for name in CODE_TEMPLATES
    ]
