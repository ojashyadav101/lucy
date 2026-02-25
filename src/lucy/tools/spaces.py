"""Lucy Spaces tool definitions and executor.

Provides 5 OpenAI-format tool definitions for building and deploying
web applications via Lucy Spaces. Follows the email_tools.py pattern.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger()


def get_spaces_tool_definitions() -> list[dict[str, Any]]:
    """Return OpenAI-format tool definitions for spaces operations."""
    return [
        {
            "type": "function",
            "function": {
                "name": "lucy_spaces_init",
                "description": (
                    "Create a new full-stack web application project. "
                    "This scaffolds a React + Convex project with auth, "
                    "53 pre-installed UI components, and hosting on zeeya.app. "
                    "After creation, write your application code, then deploy."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_name": {
                            "type": "string",
                            "description": (
                                "Name for the app (e.g. 'calculator', "
                                "'task-manager'). Will be slugified."
                            ),
                        },
                        "description": {
                            "type": "string",
                            "description": "Brief description of the app.",
                        },
                    },
                    "required": ["project_name", "description"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "lucy_spaces_deploy",
                "description": (
                    "Build and deploy a Lucy Spaces app to the web. "
                    "This automatically runs bun install + vite build, "
                    "then uploads to Vercel. No manual build step needed. "
                    "Call this after writing your code with lucy_write_file."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_name": {
                            "type": "string",
                            "description": "Name of the project to deploy.",
                        },
                        "environment": {
                            "type": "string",
                            "enum": ["preview", "production"],
                            "description": "Deploy target. Default: production.",
                        },
                    },
                    "required": ["project_name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "lucy_spaces_list",
                "description": (
                    "List all web applications you have built and deployed."
                ),
                "parameters": {"type": "object", "properties": {}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "lucy_spaces_status",
                "description": (
                    "Get detailed status of a deployed app including URLs, "
                    "deployment history, and Convex database info."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_name": {
                            "type": "string",
                            "description": "Name of the project.",
                        },
                    },
                    "required": ["project_name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "lucy_spaces_delete",
                "description": (
                    "Delete a Lucy Spaces app. Removes the Convex project, "
                    "Vercel deployment, and all local files. Irreversible."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_name": {
                            "type": "string",
                            "description": "Name of the project to delete.",
                        },
                    },
                    "required": ["project_name"],
                },
            },
        },
    ]


_SPACES_TOOL_NAMES = frozenset({
    "lucy_spaces_init",
    "lucy_spaces_deploy",
    "lucy_spaces_list",
    "lucy_spaces_status",
    "lucy_spaces_delete",
})


def is_spaces_tool(tool_name: str) -> bool:
    """Check if a tool name belongs to the spaces tool suite."""
    return tool_name in _SPACES_TOOL_NAMES


async def execute_spaces_tool(
    tool_name: str,
    parameters: dict[str, Any],
    workspace_id: str,
) -> dict[str, Any]:
    """Dispatch a spaces tool call to the platform service.

    Returns structured results with a human-readable 'summary' field
    that Lucy can relay directly to the user.
    """
    from lucy.spaces.platform import (
        delete_app_project,
        deploy_app,
        get_app_status,
        init_app_project,
        list_apps,
    )

    try:
        if tool_name == "lucy_spaces_init":
            result = await init_app_project(
                project_name=parameters.get("project_name", ""),
                description=parameters.get("description", ""),
                workspace_id=workspace_id,
            )
            if result.get("success"):
                result["summary"] = (
                    f"Project '{result['project_name']}' created. "
                    f"Write your app code to {result['sandbox_path']}/src/App.tsx, "
                    f"then call lucy_spaces_deploy to publish it."
                )
            return result

        if tool_name == "lucy_spaces_deploy":
            result = await deploy_app(
                project_name=parameters.get("project_name", ""),
                workspace_id=workspace_id,
                environment=parameters.get("environment", "production"),
            )
            if result.get("success"):
                result["summary"] = (
                    f"App deployed! Live at {result['url']}"
                )
            return result

        if tool_name == "lucy_spaces_list":
            result = await list_apps(workspace_id=workspace_id)
            if result["count"] == 0:
                result["summary"] = "No apps deployed yet."
            else:
                lines = [f"You have {result['count']} app(s):"]
                for app in result["apps"]:
                    lines.append(f"- {app['name']}: {app['url']}")
                result["summary"] = "\n".join(lines)
            return result

        if tool_name == "lucy_spaces_status":
            result = await get_app_status(
                project_name=parameters.get("project_name", ""),
                workspace_id=workspace_id,
            )
            if result.get("success"):
                result["summary"] = (
                    f"App '{result['name']}': {result['url']} "
                    f"(last deployed: {result.get('last_deployed') or 'never'})"
                )
            return result

        if tool_name == "lucy_spaces_delete":
            result = await delete_app_project(
                project_name=parameters.get("project_name", ""),
                workspace_id=workspace_id,
            )
            if result.get("success"):
                result["summary"] = "App deleted."
            return result

        return {"error": f"Unknown spaces tool: {tool_name}"}

    except Exception as e:
        logger.error(
            "spaces_tool_failed",
            tool=tool_name,
            error=str(e),
        )
        return {"error": str(e)}
