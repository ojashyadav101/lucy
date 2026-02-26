"""Coding-specific tool definitions: read, check errors, run command.

These tools are registered alongside existing file tools and give the
CodingEngine (and the main agent) the ability to read files and validate
code — capabilities that every top AI coding agent requires.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

from lucy.config import settings

logger = structlog.get_logger()

_LINE_LIMIT = 500


def get_coding_tool_definitions() -> list[dict[str, Any]]:
    """Return OpenAI-format tool definitions for coding operations."""
    return [
        {
            "type": "function",
            "function": {
                "name": "lucy_read_file",
                "description": (
                    "Read the contents of a file in the workspace. "
                    "Returns the file content with line numbers. "
                    "You MUST read a file before editing it. "
                    "Use offset and limit for large files."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "Absolute path to the file to read."
                            ),
                        },
                        "offset": {
                            "type": "integer",
                            "description": (
                                "Line number to start reading from (1-based). "
                                "Default: 1 (start of file)."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "description": (
                                "Maximum number of lines to return. "
                                f"Default: {_LINE_LIMIT}."
                            ),
                        },
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "lucy_check_errors",
                "description": (
                    "Run TypeScript type checking (tsc --noEmit) on a project "
                    "directory. Returns structured errors with file, line, and "
                    "message. Call this after writing or editing code to catch "
                    "errors before deploying. Fix any errors found before "
                    "proceeding."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_dir": {
                            "type": "string",
                            "description": (
                                "Absolute path to the project directory "
                                "(the one containing package.json / tsconfig.json)."
                            ),
                        },
                    },
                    "required": ["project_dir"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "lucy_list_files",
                "description": (
                    "List files in a directory. Returns file names, sizes, and "
                    "types. Useful for understanding project structure before "
                    "making changes."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "directory": {
                            "type": "string",
                            "description": (
                                "Absolute path to the directory to list."
                            ),
                        },
                        "recursive": {
                            "type": "boolean",
                            "description": (
                                "If true, list files recursively. "
                                "Default: false."
                            ),
                        },
                    },
                    "required": ["directory"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "lucy_save_memory",
                "description": (
                    "Save a coding preference, project pattern, or lesson "
                    "learned for future sessions. Use this to remember things "
                    "like 'user prefers dark mode UIs', 'project uses Supabase', "
                    "or 'framer-motion animations cause hydration issues'."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "category": {
                            "type": "string",
                            "enum": ["preference", "pattern", "lesson"],
                            "description": (
                                "Type of memory: preference (user likes), "
                                "pattern (project uses), lesson (learned from experience)."
                            ),
                        },
                        "key": {
                            "type": "string",
                            "description": (
                                "Key for the memory (e.g. 'ui_framework', "
                                "'database'). Required for preference and pattern."
                            ),
                        },
                        "value": {
                            "type": "string",
                            "description": (
                                "The memory content (e.g. 'tailwind', "
                                "'user prefers minimal UIs')."
                            ),
                        },
                    },
                    "required": ["category", "value"],
                },
            },
        },
    ]


def _is_within_workspace(path: Path) -> bool:
    """Check if a path is within the workspace root."""
    try:
        resolved = path.resolve()
        ws = settings.workspace_root.resolve()
        return resolved == ws or resolved.is_relative_to(ws)
    except (ValueError, Exception):
        return False


async def execute_coding_tool(
    tool_name: str,
    parameters: dict[str, Any],
    workspace_id: str = "",
) -> dict[str, Any]:
    """Execute a coding tool and return structured result."""
    try:
        if tool_name == "lucy_read_file":
            return await _execute_read_file(parameters)
        elif tool_name == "lucy_check_errors":
            return await _execute_check_errors(parameters)
        elif tool_name == "lucy_list_files":
            return await _execute_list_files(parameters)
        elif tool_name == "lucy_save_memory":
            return await _execute_save_memory(parameters, workspace_id)
        else:
            return {"error": f"Unknown coding tool: {tool_name}"}
    except Exception as e:
        logger.error("coding_tool_failed", tool=tool_name, error=str(e))
        return {"error": str(e)}


async def _execute_read_file(parameters: dict[str, Any]) -> dict[str, Any]:
    """Read a file with line numbers."""
    path_str = parameters.get("path", "")
    if not path_str:
        return {"error": "Missing required parameter: path"}

    p = Path(path_str)
    if not p.exists():
        return {"error": f"File not found: {path_str}"}
    if not p.is_file():
        return {"error": f"Not a file: {path_str}"}

    if not _is_within_workspace(p):
        return {"error": f"File is outside the workspace: {path_str}"}

    try:
        content = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {"error": f"Cannot read binary file: {path_str}"}

    lines = content.splitlines()
    total_lines = len(lines)

    offset = max(1, parameters.get("offset", 1))
    limit = min(parameters.get("limit", _LINE_LIMIT), _LINE_LIMIT)

    start = offset - 1
    end = min(start + limit, total_lines)
    selected = lines[start:end]

    numbered = "\n".join(
        f"{i + offset:4d} | {line}"
        for i, line in enumerate(selected)
    )

    result: dict[str, Any] = {
        "content": numbered,
        "total_lines": total_lines,
        "showing": f"lines {offset}-{end} of {total_lines}",
        "file_path": str(p),
    }

    if end < total_lines:
        result["truncated"] = True
        result["next_offset"] = end + 1

    logger.info(
        "file_read",
        path=str(p),
        lines_shown=len(selected),
        total_lines=total_lines,
    )
    return result


async def _execute_check_errors(parameters: dict[str, Any]) -> dict[str, Any]:
    """Run type checking on a project directory."""
    dir_str = parameters.get("project_dir", "")
    if not dir_str:
        return {"error": "Missing required parameter: project_dir"}

    project_dir = Path(dir_str)
    if not project_dir.exists():
        return {"error": f"Directory not found: {dir_str}"}

    from lucy.coding.validator import check_typescript

    result = await check_typescript(project_dir)

    if result.ok:
        return {
            "result": "No errors found. Code is valid.",
            "error_count": 0,
        }

    error_list = [
        {
            "file": e.file,
            "line": e.line,
            "column": e.column,
            "message": e.message,
        }
        for e in result.errors[:15]
    ]
    return {
        "result": result.error_summary(),
        "errors": error_list,
        "error_count": len(result.errors),
        "instructions": (
            "Fix these errors using lucy_edit_file, then call "
            "lucy_check_errors again to verify. Max 3 fix attempts."
        ),
    }


async def _execute_list_files(parameters: dict[str, Any]) -> dict[str, Any]:
    """List files in a directory."""
    dir_str = parameters.get("directory", "")
    if not dir_str:
        return {"error": "Missing required parameter: directory"}

    directory = Path(dir_str)
    if not directory.exists():
        return {"error": f"Directory not found: {dir_str}"}
    if not directory.is_dir():
        return {"error": f"Not a directory: {dir_str}"}

    if not _is_within_workspace(directory):
        return {"error": f"Directory is outside the workspace: {dir_str}"}

    recursive = parameters.get("recursive", False)
    entries: list[dict[str, Any]] = []

    skip_dirs = {"node_modules", ".git", "dist", "__pycache__", ".next", ".venv"}

    if recursive:
        for item in sorted(directory.rglob("*")):
            if any(part in skip_dirs for part in item.parts):
                continue
            if len(entries) >= 200:
                break
            rel = item.relative_to(directory)
            entry: dict[str, Any] = {
                "path": str(rel),
                "type": "dir" if item.is_dir() else "file",
            }
            if item.is_file():
                entry["size"] = item.stat().st_size
            entries.append(entry)
    else:
        for item in sorted(directory.iterdir()):
            if item.name in skip_dirs:
                continue
            entry = {
                "name": item.name,
                "type": "dir" if item.is_dir() else "file",
            }
            if item.is_file():
                entry["size"] = item.stat().st_size
            entries.append(entry)

    return {
        "directory": str(directory),
        "entries": entries,
        "count": len(entries),
    }


async def _execute_save_memory(
    parameters: dict[str, Any],
    workspace_id: str,
) -> dict[str, Any]:
    """Save a coding memory entry."""
    if not workspace_id:
        return {"error": "No workspace context for saving memory"}

    category = parameters.get("category", "")
    key = parameters.get("key", "")
    value = parameters.get("value", "")

    if not value:
        return {"error": "Missing required parameter: value"}
    if category in ("preference", "pattern") and not key:
        return {"error": "Missing required parameter: key (for preference/pattern)"}

    from lucy.coding.memory import load_coding_memory, save_coding_memory

    memory = load_coding_memory(workspace_id)

    if category == "preference":
        memory.add_preference(key, value)
    elif category == "pattern":
        memory.add_pattern(key, value)
    elif category == "lesson":
        memory.add_lesson(value)
    else:
        return {"error": f"Unknown category: {category}"}

    save_coding_memory(workspace_id, memory)

    logger.info(
        "coding_memory_saved_via_tool",
        workspace_id=workspace_id,
        category=category,
        key=key,
    )
    return {"result": f"Saved {category}: {key or value[:50]}"}


_CODING_TOOL_NAMES = frozenset({
    "lucy_read_file",
    "lucy_check_errors",
    "lucy_list_files",
    "lucy_save_memory",
})


def is_coding_tool(tool_name: str) -> bool:
    """Check if a tool name belongs to the coding tool suite."""
    return tool_name in _CODING_TOOL_NAMES
