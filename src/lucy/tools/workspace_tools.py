"""Workspace management tools — let the agent manage its own persistent state.

Viktor's core advantage: it can read, write, and search its own workspace.
These tools give Lucy the same capability.

Tools:
- lucy_workspace_read: Read any file from the workspace
- lucy_workspace_write: Write/create files in the workspace
- lucy_workspace_list: List files and directories
- lucy_workspace_search: Full-text search across workspace files
- lucy_manage_skill: Create, read, or update skill files
"""

from __future__ import annotations

import re
from typing import Any

import structlog

from lucy.workspace.filesystem import WorkspaceFS, get_workspace
from lucy.workspace.skills import (
    SkillInfo,
    list_skills,
    parse_frontmatter,
    read_skill,
    write_skill,
)

logger = structlog.get_logger()

# Safety: paths that must never be written to by the agent
_PROTECTED_PATHS = re.compile(
    r"^(?:\.\.|\.\./|state\.json$|data/backups/)",
)

# Max file read size to prevent token blowup
_MAX_READ_CHARS = 50_000
_MAX_SEARCH_RESULTS = 30
_MAX_LIST_ENTRIES = 100


def get_workspace_tool_definitions() -> list[dict[str, Any]]:
    """Return OpenAI-format tool definitions for workspace management."""
    return [
        {
            "type": "function",
            "function": {
                "name": "lucy_workspace_read",
                "description": (
                    "Read a file from your persistent workspace. Use this to "
                    "check what you know (skills, company info, team data), "
                    "review logs, or read any saved file. Common paths: "
                    "company/SKILL.md, team/SKILL.md, skills/{name}/SKILL.md, "
                    "data/session_memory.json"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "Relative path within workspace, e.g. "
                                "'company/SKILL.md', 'skills/browser/SKILL.md', "
                                "'data/session_memory.json'"
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
                "name": "lucy_workspace_write",
                "description": (
                    "Write or update a file in your persistent workspace. "
                    "Use this to save notes, update skills, persist learned "
                    "information, or create new files. Content persists across "
                    "all future conversations."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "Relative path within workspace, e.g. "
                                "'skills/my-skill/SKILL.md', 'data/notes.md'"
                            ),
                        },
                        "content": {
                            "type": "string",
                            "description": "File content to write.",
                        },
                        "append": {
                            "type": "boolean",
                            "description": (
                                "If true, append to existing file instead "
                                "of overwriting. Default: false."
                            ),
                        },
                    },
                    "required": ["path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "lucy_workspace_list",
                "description": (
                    "List files and directories in your workspace. "
                    "Use this to discover what skills, data, and files exist. "
                    "Start with '.' for root or 'skills/' for all skills."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": (
                                "Directory to list, relative to workspace root. "
                                "Default: '.' (root). Examples: 'skills/', "
                                "'company/', 'data/'"
                            ),
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "lucy_workspace_search",
                "description": (
                    "Search for text across all workspace files (skills, "
                    "notes, data, logs). Case-insensitive. Use this to find "
                    "information you may have stored previously or to check "
                    "if you already know something."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Text to search for across workspace files.",
                        },
                        "directory": {
                            "type": "string",
                            "description": (
                                "Optional: limit search to a subdirectory. "
                                "Default: entire workspace."
                            ),
                        },
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "lucy_manage_skill",
                "description": (
                    "Create, read, or update a skill file. Skills are your "
                    "persistent knowledge and workflows — they survive forever "
                    "and are available in every future conversation. Create "
                    "skills when you learn reusable processes, discover team "
                    "preferences, or build workflows worth remembering."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["create", "read", "update", "list"],
                            "description": (
                                "Action: 'create' a new skill, 'read' an "
                                "existing one, 'update' an existing one, or "
                                "'list' all available skills."
                            ),
                        },
                        "skill_name": {
                            "type": "string",
                            "description": (
                                "Skill name (lowercase, hyphens). Required "
                                "for create/read/update. E.g. 'meeting-notes', "
                                "'deployment-process', 'client-onboarding'"
                            ),
                        },
                        "content": {
                            "type": "string",
                            "description": (
                                "Full skill content including frontmatter. "
                                "Required for create/update. Must include "
                                "---\\nname: skill-name\\ndescription: ...\\n---"
                            ),
                        },
                        "subdirectory": {
                            "type": "string",
                            "description": (
                                "Where to store the skill. Default: 'skills'. "
                                "Use 'company' or 'team' for those specific "
                                "knowledge bases."
                            ),
                        },
                    },
                    "required": ["action"],
                },
            },
        },
    ]


def is_workspace_tool(tool_name: str) -> bool:
    """Check if a tool name is a workspace management tool."""
    return tool_name in {
        "lucy_workspace_read",
        "lucy_workspace_write",
        "lucy_workspace_list",
        "lucy_workspace_search",
        "lucy_manage_skill",
    }


async def execute_workspace_tool(
    tool_name: str,
    parameters: dict[str, Any],
    workspace_id: str,
) -> dict[str, Any]:
    """Execute a workspace management tool."""
    ws = get_workspace(workspace_id)

    try:
        if tool_name == "lucy_workspace_read":
            return await _handle_read(ws, parameters)
        elif tool_name == "lucy_workspace_write":
            return await _handle_write(ws, parameters)
        elif tool_name == "lucy_workspace_list":
            return await _handle_list(ws, parameters)
        elif tool_name == "lucy_workspace_search":
            return await _handle_search(ws, parameters)
        elif tool_name == "lucy_manage_skill":
            return await _handle_manage_skill(ws, parameters)
        else:
            return {"error": f"Unknown workspace tool: {tool_name}"}
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        logger.error("workspace_tool_error", tool=tool_name, error=str(e))
        return {"error": f"Workspace operation failed: {str(e)}"}


# ═══════════════════════════════════════════════════════════════════════════
# Tool Handlers
# ═══════════════════════════════════════════════════════════════════════════

async def _handle_read(
    ws: WorkspaceFS,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Read a file from the workspace."""
    path = params.get("path", "").strip()
    if not path:
        return {"error": "No path provided"}

    if _PROTECTED_PATHS.search(path):
        return {"error": f"Cannot read protected path: {path}"}

    content = await ws.read_file(path)
    if content is None:
        return {
            "error": f"File not found: {path}",
            "hint": "Use lucy_workspace_list to see available files.",
        }

    # Truncate if too large
    if len(content) > _MAX_READ_CHARS:
        content = content[:_MAX_READ_CHARS] + f"\n\n[... truncated at {_MAX_READ_CHARS} chars]"

    logger.info("workspace_read", path=path, chars=len(content))
    return {"path": path, "content": content}


async def _handle_write(
    ws: WorkspaceFS,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Write or append to a file in the workspace."""
    path = params.get("path", "").strip()
    content = params.get("content", "")
    append = params.get("append", False)

    if not path:
        return {"error": "No path provided"}

    if _PROTECTED_PATHS.search(path):
        return {"error": f"Cannot write to protected path: {path}"}

    # Safety: prevent writing outside workspace
    if ".." in path:
        return {"error": "Path traversal not allowed"}

    if append:
        await ws.append_file(path, content)
        logger.info("workspace_appended", path=path, chars=len(content))
        return {"success": True, "path": path, "action": "appended"}
    else:
        # Back up existing file before overwriting
        existing = await ws.read_file(path)
        if existing is not None:
            await ws.backup_file(path)

        await ws.write_file(path, content)
        logger.info("workspace_written", path=path, chars=len(content))
        return {
            "success": True,
            "path": path,
            "action": "created" if existing is None else "updated",
        }


async def _handle_list(
    ws: WorkspaceFS,
    params: dict[str, Any],
) -> dict[str, Any]:
    """List files and directories in the workspace."""
    path = params.get("path", ".").strip()

    entries = await ws.list_dir(path)
    if not entries:
        return {"path": path, "entries": [], "note": "Directory is empty or does not exist."}

    # Limit entries to prevent token blowup
    truncated = len(entries) > _MAX_LIST_ENTRIES
    entries = entries[:_MAX_LIST_ENTRIES]

    return {
        "path": path,
        "entries": entries,
        "count": len(entries),
        **({"truncated": True, "note": f"Showing first {_MAX_LIST_ENTRIES} entries"} if truncated else {}),
    }


async def _handle_search(
    ws: WorkspaceFS,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Search across workspace files."""
    query = params.get("query", "").strip()
    directory = params.get("directory", ".").strip()

    if not query:
        return {"error": "No search query provided"}

    results = await ws.search(query, directory)

    if not results:
        return {
            "query": query,
            "results": [],
            "note": "No matches found. Try different keywords.",
        }

    # Limit results
    truncated = len(results) > _MAX_SEARCH_RESULTS
    results = results[:_MAX_SEARCH_RESULTS]

    return {
        "query": query,
        "results": results,
        "count": len(results),
        **({"truncated": True} if truncated else {}),
    }


async def _handle_manage_skill(
    ws: WorkspaceFS,
    params: dict[str, Any],
) -> dict[str, Any]:
    """Create, read, update, or list skills."""
    action = params.get("action", "").strip().lower()

    if action == "list":
        skills = await list_skills(ws)
        if not skills:
            return {
                "skills": [],
                "note": "No skills found. Create one with action='create'.",
            }
        return {
            "skills": [
                {"name": s.name, "description": s.description, "path": s.path}
                for s in sorted(skills, key=lambda s: s.name)
            ],
            "count": len(skills),
        }

    skill_name = params.get("skill_name", "").strip()
    if not skill_name:
        return {"error": "skill_name is required for create/read/update actions"}

    subdirectory = params.get("subdirectory", "skills").strip()

    if action == "read":
        # Find the skill by name
        skills = await list_skills(ws)
        match = next((s for s in skills if s.name == skill_name), None)
        if match:
            content = await read_skill(ws, match.path)
            return {"name": skill_name, "path": match.path, "content": content}
        # Try direct path
        direct_path = f"{subdirectory}/{skill_name}/SKILL.md"
        content = await ws.read_file(direct_path)
        if content:
            return {"name": skill_name, "path": direct_path, "content": content}
        return {"error": f"Skill '{skill_name}' not found"}

    if action in ("create", "update"):
        content = params.get("content", "").strip()
        if not content:
            return {"error": "content is required for create/update"}

        # Validate frontmatter
        metadata, body = parse_frontmatter(content)
        if not metadata.get("name"):
            return {
                "error": "Skill content must include frontmatter with 'name' field",
                "hint": "Start with: ---\\nname: skill-name\\ndescription: What it does.\\n---",
            }
        if not metadata.get("description"):
            return {
                "error": "Skill content must include 'description' in frontmatter",
                "hint": "Add: description: What it does. Use when X.",
            }

        # For update, back up existing
        if action == "update":
            existing_path = f"{subdirectory}/{skill_name}/SKILL.md"
            await ws.backup_file(existing_path)

        path = await write_skill(ws, skill_name, content, subdirectory=subdirectory)
        logger.info(
            "skill_managed",
            action=action,
            skill_name=skill_name,
            path=path,
        )
        return {
            "success": True,
            "action": action,
            "name": skill_name,
            "path": path,
            "note": (
                "Skill saved. It will be available in all future conversations."
                if action == "create"
                else "Skill updated. Changes take effect in the next conversation."
            ),
        }

    return {"error": f"Unknown action: {action}. Use: create, read, update, list"}
