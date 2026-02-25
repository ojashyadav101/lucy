"""Email tool definitions and executor for Lucy's native email identity.

Provides 5 OpenAI-format tool definitions and a dispatcher that routes
tool calls to the ``LucyEmailClient``.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger()


def get_email_tool_definitions() -> list[dict[str, Any]]:
    """Return OpenAI-format tool definitions for email operations."""
    return [
        {
            "type": "function",
            "function": {
                "name": "lucy_send_email",
                "description": (
                    "Send an email from your own email address (lucy@zeeyamail.com). "
                    "Use this for outbound communication, introductions, follow-ups, "
                    "or any task where you need to email someone directly."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "to": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Recipient email addresses.",
                        },
                        "subject": {
                            "type": "string",
                            "description": "Email subject line.",
                        },
                        "body": {
                            "type": "string",
                            "description": "Plain text email body.",
                        },
                        "html": {
                            "type": "string",
                            "description": (
                                "Optional HTML body for rich formatting. "
                                "If provided, recipients see this instead of plain text."
                            ),
                        },
                        "cc": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional CC recipients.",
                        },
                        "bcc": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional BCC recipients.",
                        },
                    },
                    "required": ["to", "subject", "body"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "lucy_read_emails",
                "description": (
                    "Read recent email threads in your inbox. Returns thread "
                    "summaries with subject, sender, date, and message count."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Maximum threads to return. Default: 10.",
                            "default": 10,
                        },
                        "labels": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional labels to filter by.",
                        },
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "lucy_reply_to_email",
                "description": (
                    "Reply to a specific email message. Use the message_id "
                    "from lucy_read_emails or lucy_get_email_thread."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "message_id": {
                            "type": "string",
                            "description": "The message_id to reply to.",
                        },
                        "body": {
                            "type": "string",
                            "description": "Plain text reply body.",
                        },
                        "html": {
                            "type": "string",
                            "description": "Optional HTML body for rich formatting.",
                        },
                    },
                    "required": ["message_id", "body"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "lucy_search_emails",
                "description": (
                    "Search your inbox for emails matching a query. "
                    "Searches across subject lines, body text, and sender."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search term or phrase.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum results. Default: 10.",
                            "default": 10,
                        },
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "lucy_get_email_thread",
                "description": (
                    "Get the full conversation thread with all messages. "
                    "Use a thread_id from lucy_read_emails results."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "thread_id": {
                            "type": "string",
                            "description": "The thread_id to retrieve.",
                        },
                    },
                    "required": ["thread_id"],
                },
            },
        },
    ]


_EMAIL_TOOL_NAMES = frozenset({
    "lucy_send_email",
    "lucy_read_emails",
    "lucy_reply_to_email",
    "lucy_search_emails",
    "lucy_get_email_thread",
})


def is_email_tool(tool_name: str) -> bool:
    """Check if a tool name belongs to the email tool suite."""
    return tool_name in _EMAIL_TOOL_NAMES


async def execute_email_tool(
    tool_name: str,
    parameters: dict[str, Any],
) -> dict[str, Any]:
    """Dispatch an email tool call to the AgentMail client."""
    from lucy.integrations.agentmail_client import get_email_client

    try:
        client = get_email_client()
    except RuntimeError as exc:
        return {"error": str(exc)}

    try:
        if tool_name == "lucy_send_email":
            return await client.send_email(
                to=parameters.get("to", []),
                subject=parameters.get("subject", ""),
                text=parameters.get("body", ""),
                html=parameters.get("html"),
                cc=parameters.get("cc"),
                bcc=parameters.get("bcc"),
            )

        if tool_name == "lucy_read_emails":
            threads = await client.list_threads(
                limit=parameters.get("limit", 10),
                labels=parameters.get("labels"),
            )
            return {"threads": threads, "count": len(threads)}

        if tool_name == "lucy_reply_to_email":
            return await client.reply_to_email(
                message_id=parameters["message_id"],
                text=parameters.get("body", ""),
                html=parameters.get("html"),
            )

        if tool_name == "lucy_search_emails":
            results = await client.search_messages(
                query=parameters["query"],
                limit=parameters.get("limit", 10),
            )
            return {"results": results, "count": len(results)}

        if tool_name == "lucy_get_email_thread":
            thread = await client.get_thread(
                thread_id=parameters["thread_id"],
            )
            return {"thread": thread}

        return {"error": f"Unknown email tool: {tool_name}"}

    except Exception as e:
        logger.error(
            "email_tool_failed",
            tool=tool_name,
            error=str(e),
        )
        return {"error": str(e)}
