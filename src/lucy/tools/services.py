"""Persistent background service tools.

Exposes OpenClaw Gateway's background process management as agent tools,
enabling Lucy to start, stop, monitor, and list always-running services
such as webhooks, event listeners, and background workers.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger()


def get_services_tool_definitions() -> list[dict[str, Any]]:
    """Return tool definitions for persistent service management."""
    return [
        {
            "type": "function",
            "function": {
                "name": "lucy_start_service",
                "description": (
                    "Start a persistent background service (always-running process) on the VPS. "
                    "Use this for: webhook listeners, event processors, long-running workers, "
                    "polling scripts that need to run continuously. "
                    "Returns a service_id you can use to check status or stop the service. "
                    "Use crons for periodic tasks instead — services are for continuous operation."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": (
                                "The shell command to run as a background service. "
                                "E.g.: 'python /path/to/webhook_listener.py' or "
                                "'node server.js'"
                            ),
                        },
                        "name": {
                            "type": "string",
                            "description": (
                                "Human-readable name for this service "
                                "(e.g. 'stripe-webhook-listener', 'email-processor')"
                            ),
                        },
                        "workdir": {
                            "type": "string",
                            "description": "Working directory for the command (optional)",
                        },
                    },
                    "required": ["command", "name"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "lucy_stop_service",
                "description": (
                    "Stop a running background service. "
                    "Use when a service is no longer needed or needs to be restarted."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "service_id": {
                            "type": "string",
                            "description": "The service_id returned by lucy_start_service",
                        },
                    },
                    "required": ["service_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "lucy_list_services",
                "description": (
                    "List all background services (running and recently stopped). "
                    "Shows service name, status, start time, and service_id."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "lucy_service_logs",
                "description": (
                    "Get recent logs from a running or stopped background service. "
                    "Useful for debugging or verifying that a service is working correctly."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "service_id": {
                            "type": "string",
                            "description": "The service_id to fetch logs for",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of log lines to return (default: 100)",
                        },
                    },
                    "required": ["service_id"],
                },
            },
        },
    ]


async def execute_service_tool(
    tool_name: str,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    """Execute a persistent service tool call."""
    from lucy.integrations.openclaw_gateway import get_gateway_client

    try:
        client = await get_gateway_client()
    except RuntimeError as e:
        return {"error": f"Service management not available: {e}"}

    if tool_name == "lucy_start_service":
        command = parameters.get("command", "")
        name = parameters.get("name", "unnamed-service")
        workdir = parameters.get("workdir")
        if not command:
            return {"error": "command is required"}
        try:
            session_id = await client.start_background(
                command=command,
                workdir=workdir,
            )
            logger.info(
                "service_started",
                name=name,
                session_id=session_id,
                command=command[:80],
            )
            return {
                "service_id": session_id,
                "name": name,
                "status": "running",
                "message": f"Service '{name}' is now running.",
            }
        except Exception as e:
            logger.error("service_start_error", error=str(e))
            return {"error": f"Failed to start service: {e}"}

    elif tool_name == "lucy_stop_service":
        service_id = parameters.get("service_id", "")
        if not service_id:
            return {"error": "service_id is required"}
        try:
            result = await client.kill_process(service_id)
            logger.info("service_stopped", service_id=service_id)
            return {"status": "stopped", "service_id": service_id, "result": result}
        except Exception as e:
            logger.error("service_stop_error", service_id=service_id, error=str(e))
            return {"error": f"Failed to stop service: {e}"}

    elif tool_name == "lucy_list_services":
        try:
            processes = await client.list_processes()
            services = []
            for proc in processes:
                services.append({
                    "service_id": proc.get("sessionId", ""),
                    "command": proc.get("command", "")[:80],
                    "status": proc.get("status", "unknown"),
                    "started_at": proc.get("startedAt", ""),
                })
            return {"services": services, "count": len(services)}
        except Exception as e:
            logger.error("service_list_error", error=str(e))
            return {"error": f"Failed to list services: {e}"}

    elif tool_name == "lucy_service_logs":
        service_id = parameters.get("service_id", "")
        limit = parameters.get("limit", 100)
        if not service_id:
            return {"error": "service_id is required"}
        try:
            result = await client.log_process(session_id=service_id, limit=limit)
            return result
        except Exception as e:
            logger.error("service_logs_error", service_id=service_id, error=str(e))
            return {"error": f"Failed to get logs: {e}"}

    return {"error": f"Unknown service tool: {tool_name}"}


def is_service_tool(tool_name: str) -> bool:
    """Check if a tool name belongs to the services module."""
    return tool_name in {
        "lucy_start_service",
        "lucy_stop_service",
        "lucy_list_services",
        "lucy_service_logs",
    }
