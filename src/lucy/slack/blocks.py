"""Block Kit message composition for Lucy.

Standardized message formats for:
- Simple responses
- Task confirmations
- Approval requests
- Error messages
- Thinking/loading states
"""

from __future__ import annotations

from typing import Any
from uuid import UUID


def section(text: str, accessory: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create a section block."""
    block: dict[str, Any] = {
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": text,
        },
    }
    if accessory:
        block["accessory"] = accessory
    return block


def context(elements: list[dict[str, Any]]) -> dict[str, Any]:
    """Create a context block."""
    return {
        "type": "context",
        "elements": elements,
    }


def divider() -> dict[str, Any]:
    """Create a divider block."""
    return {"type": "divider"}


def button(
    text: str,
    action_id: str,
    value: str | None = None,
    style: str | None = None,
    url: str | None = None,
) -> dict[str, Any]:
    """Create a button element."""
    btn: dict[str, Any] = {
        "type": "button",
        "text": {
            "type": "plain_text",
            "text": text,
            "emoji": True,
        },
        "action_id": action_id,
    }
    if value:
        btn["value"] = value
    if style:
        btn["style"] = style
    if url:
        btn["url"] = url
    return btn


def actions(elements: list[dict[str, Any]]) -> dict[str, Any]:
    """Create an actions block."""
    return {
        "type": "actions",
        "elements": elements,
    }


def header(text: str) -> dict[str, Any]:
    """Create a header block."""
    return {
        "type": "header",
        "text": {
            "type": "plain_text",
            "text": text,
            "emoji": True,
        },
    }


def mrkdwn(text: str) -> dict[str, Any]:
    """Create mrkdwn text object."""
    return {
        "type": "mrkdwn",
        "text": text,
    }


def plain_text(text: str, emoji: bool = True) -> dict[str, Any]:
    """Create plain_text object."""
    return {
        "type": "plain_text",
        "text": text,
        "emoji": emoji,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# LUCY MESSAGE TEMPLATES
# ═══════════════════════════════════════════════════════════════════════════════

class LucyMessage:
    """Standard Lucy message templates."""

    @staticmethod
    def simple_response(
        text: str,
        emoji: str = "🤝",
    ) -> list[dict[str, Any]]:
        """Simple text response."""
        return [
            section(f"{emoji} {text}"),
        ]

    @staticmethod
    def thinking(
        task_type: str = "processing",
    ) -> list[dict[str, Any]]:
        """Loading/thinking state."""
        return [
            context([
                {
                    "type": "image",
                    "image_url": "https://slack-imgs.com/?c=1&o1=wi32.he32&url=https%3A%2F%2Femoji.slack-edge.com%2FT024H7BRB%2Floading%2F6b7c6b7c6b7c6b7c.gif",
                    "alt_text": "Thinking",
                },
                plain_text(f"Lucy is {task_type} your request..."),
            ]),
        ]

    @staticmethod
    def task_confirmation(
        task_id: UUID,
        description: str,
        estimated_time: str = "~30 seconds",
    ) -> list[dict[str, Any]]:
        """Task accepted confirmation."""
        return [
            header("Task Accepted"),
            section(f"*{description}*"),
            context([
                mrkdwn(f"⏱️ Estimated: {estimated_time} | ID: `{task_id}`"),
            ]),
            divider(),
            actions([
                button(
                    "View Details",
                    f"lucy_action_view_task:{task_id}",
                    value=str(task_id),
                ),
            ]),
        ]

    @staticmethod
    def connection_request(
        provider_name: str,
        oauth_url: str,
    ) -> list[dict[str, Any]]:
        """Request user to authenticate an integration."""
        provider_display = provider_name.title()
        return [
            header(f"🔌 Connect {provider_display}"),
            section(
                f"Lucy needs access to *{provider_display}* to complete this task. "
                "Click the button below to authenticate securely."
            ),
            actions([
                button(
                    f"Connect {provider_display}",
                    action_id=f"lucy_action_connect:{provider_name.lower()}",
                    url=oauth_url,
                    style="primary",
                ),
            ]),
            context([
                mrkdwn("You only need to do this once. The connection is securely isolated to this workspace."),
            ]),
        ]

    @staticmethod
    def approval_request(
        approval_id: UUID,
        action_type: str,
        description: str,
        risk_level: str = "medium",
        requester_name: str = "Someone",
    ) -> list[dict[str, Any]]:
        """Human-in-the-loop approval request."""
        # Risk level styling
        risk_emoji = {
            "low": "🟢",
            "medium": "🟡",
            "high": "🔴",
            "critical": "⚠️",
        }.get(risk_level, "🟡")

        risk_text = f"{risk_emoji} Risk: *{risk_level.upper()}*"

        return [
            header(f"Approval Required: {action_type.replace('_', ' ').title()}"),
            section(f"*{description}*"),
            context([
                mrkdwn(f"Requested by: {requester_name}"),
                mrkdwn(risk_text),
            ]),
            divider(),
            actions([
                button(
                    "Approve",
                    f"lucy_action_approve:{approval_id}",
                    value=str(approval_id),
                    style="primary",
                ),
                button(
                    "Reject",
                    f"lucy_action_reject:{approval_id}",
                    value=str(approval_id),
                    style="danger",
                ),
                button(
                    "View Details",
                    f"lucy_action_view_approval:{approval_id}",
                    value=str(approval_id),
                ),
            ]),
        ]

    @staticmethod
    def task_result(
        task_id: UUID,
        result_summary: str,
        success: bool = True,
        duration: str | None = None,
        actions_list: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Task completion result."""
        emoji = "✅" if success else "❌"
        header_text = "Complete" if success else "Failed"

        blocks: list[dict[str, Any]] = [
            header(f"{emoji} Task {header_text}"),
            section(result_summary),
        ]

        if duration:
            blocks.append(context([
                mrkdwn(f"⏱️ Duration: {duration} | ID: `{task_id}`"),
            ]))

        if actions_list:
            blocks.append(divider())
            blocks.append(actions(actions_list))

        return blocks

    @staticmethod
    def error(
        message: str,
        error_code: str | None = None,
        suggestion: str | None = None,
    ) -> list[dict[str, Any]]:
        """Error message with optional suggestion."""
        blocks: list[dict[str, Any]] = [
            header("⚠️ Something went wrong"),
            section(message),
        ]

        if error_code:
            blocks.append(context([
                mrkdwn(f"Error code: `{error_code}`"),
            ]))

        if suggestion:
            blocks.append(section(f"*Suggestion:* {suggestion}"))

        return blocks

    @staticmethod
    def help() -> list[dict[str, Any]]:
        """Help message with available commands."""
        return [
            header("🤝 Lucy — Your AI Coworker"),
            section(
                "I can help you with a variety of tasks:\n\n"
                "• *Ask questions* — @Lucy what's our Q3 revenue?\n"
                "• *Run reports* — @Lucy generate weekly summary\n"
                "• *Connect tools* — @Lucy connect Linear\n"
                "• *Schedule workflows* — @Lucy run this daily at 9am\n"
                "• *Get insights* — @Lucy what did I miss yesterday?"
            ),
            divider(),
            section(
                "*Slash commands:*\n"
                "• `/lucy help` — Show this message\n"
                "• `/lucy status` — Check my status\n"
                "• `/lucy schedule` — Manage scheduled workflows"
            ),
            context([
                mrkdwn("Type `@Lucy` followed by your request to get started!"),
            ]),
        ]

    @staticmethod
    def status(
        is_healthy: bool = True,
        pending_tasks: int = 0,
        connected_integrations: int = 0,
    ) -> list[dict[str, Any]]:
        """System status message."""
        status_emoji = "🟢" if is_healthy else "🔴"

        return [
            header(f"{status_emoji} Lucy Status"),
            section(
                f"*Status:* {'All systems operational' if is_healthy else 'Degraded'}\n"
                f"*Pending tasks:* {pending_tasks}\n"
                f"*Connected tools:* {connected_integrations}"
            ),
        ]


# Legacy alias for compatibility
message = LucyMessage()
