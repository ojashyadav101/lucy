"""Confirmation Gate — intercepts tool calls that require user approval.

This module sits between the agent's tool dispatch and actual execution.
When a tool is classified as WRITE or DESTRUCTIVE, the gate:

1. Formats a human-readable description of the action
2. Creates a pending action via the HITL system
3. Returns a "pending_approval" result to the agent
4. The agent presents approval UI to the user via Block Kit buttons
5. On approval, execution resumes through the HITL callback

For READ actions, the gate is a no-op — execution proceeds immediately.

The gate also handles COMPOSIO_MULTI_EXECUTE_TOOL specially, inspecting
inner actions to determine the aggregate risk level.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

from lucy.core.action_classifier import (
    ActionType,
    classify,
    classify_composio_multi_execute,
    get_classification_summary,
)

logger = structlog.get_logger()

# Actions that should NEVER be gated (internal orchestration)
_GATE_EXEMPT: frozenset[str] = frozenset({
    # Composio discovery tools — needed for the agent to find tools
    "COMPOSIO_SEARCH_TOOLS",
    "COMPOSIO_GET_TOOL_SCHEMAS",
    "COMPOSIO_MANAGE_CONNECTIONS",
    # Internal read tools the agent uses constantly
    "lucy_list_crons",
    "lucy_list_heartbeats",
    "lucy_list_files",
    "lucy_read_file",
    "lucy_search_slack_history",
    "lucy_get_channel_history",
    "lucy_web_search",
    # Integration research — the user asking to connect a service IS the consent;
    # asking "are you sure you want me to research this?" is redundant and breaks UX
    "lucy_resolve_custom_integration",
    "lucy_list_mcp_connections",
    "lucy_refresh_mcp",
})

# Tools where gating would break the UX (the action IS the user's request)
# These produce artifacts the user requested — blocking them would be weird.
# E.g., "Generate me a PDF" → gate would ask "Can I generate this PDF?" — pointless.
_IMPLICIT_CONSENT_TOOLS: frozenset[str] = frozenset({
    "lucy_generate_pdf",
    "lucy_generate_excel",
    "lucy_generate_docx",
    "lucy_generate_pptx",
    "lucy_generate_image",
    "lucy_write_file",
    "lucy_edit_file",
    "lucy_spaces_deploy",
})


def should_gate(
    tool_name: str,
    parameters: dict[str, Any] | None = None,
    is_cron_execution: bool = False,
) -> tuple[bool, ActionType]:
    """Determine if a tool call should be gated for user confirmation.

    Returns (should_gate: bool, action_type: ActionType).

    Gating rules:
    - READ actions: never gated
    - Gate-exempt tools: never gated (internal orchestration)
    - Implicit-consent tools: never gated (user explicitly asked for the output)
    - Cron executions: only DESTRUCTIVE actions are gated (WRITE auto-approved)
    - WRITE actions: gated in interactive mode
    - DESTRUCTIVE actions: always gated
    """
    # Exempt tools always pass through
    if tool_name in _GATE_EXEMPT:
        return False, ActionType.READ

    # Implicit consent tools — user asked for this
    if tool_name in _IMPLICIT_CONSENT_TOOLS:
        return False, ActionType.WRITE

    # Special handling for COMPOSIO_MULTI_EXECUTE_TOOL
    if tool_name == "COMPOSIO_MULTI_EXECUTE_TOOL" and parameters:
        actions = parameters.get("tools") or parameters.get("actions") or []
        action_type = classify_composio_multi_execute(actions)
    else:
        action_type = classify(tool_name, parameters)

    # READ actions always pass through
    if action_type == ActionType.READ:
        return False, action_type

    # During cron execution, auto-approve WRITE, gate DESTRUCTIVE
    if is_cron_execution:
        if action_type == ActionType.WRITE:
            return False, action_type
        # DESTRUCTIVE in cron = log warning, still gate
        logger.warning(
            "confirmation_gate_destructive_in_cron",
            tool=tool_name,
            action_type=action_type.value,
        )
        return True, action_type

    # Interactive mode: gate both WRITE and DESTRUCTIVE
    return True, action_type


def format_confirmation_message(
    tool_name: str,
    parameters: dict[str, Any],
    action_type: ActionType,
) -> str:
    """Generate a natural first-person narrative for the approval prompt.

    Returns a conversational description the user can read and act on.
    """
    return _build_hitl_narrative(tool_name, parameters, action_type)


def _build_hitl_narrative(
    tool_name: str,
    parameters: dict[str, Any],
    action_type: ActionType,
) -> str:
    """Build a natural, contextual narrative for each gated tool call.

    Outputs first-person language that describes exactly what Lucy is
    about to do, so the user can make an informed approval decision.
    DESTRUCTIVE actions always include a ⚠️ warning prefix so severity is
    visible in both Block Kit and plain-text rendering.
    """
    is_destructive = action_type == ActionType.DESTRUCTIVE
    body = _build_hitl_narrative_inner(tool_name, parameters, is_destructive)
    if is_destructive:
        return f"⚠️  *Destructive action — cannot be undone*\n\n{body}"
    return body


def _build_hitl_narrative_inner(
    tool_name: str,
    parameters: dict[str, Any],
    is_destructive: bool,
) -> str:

    # ── MCP connection ────────────────────────────────────────────────
    if tool_name in ("lucy_connect_mcp", "lucy_custom_connect_mcp"):
        service = (parameters.get("service") or "the service").replace("_", " ").title()
        url = parameters.get("mcp_url") or parameters.get("url", "")
        short_url = (url[:72] + "…") if len(url) > 72 else url
        return (
            f"To connect *{service}* via MCP, I'll use this endpoint:\n"
            f"> `{short_url}`\n\n"
            "Say the word and I'll set it up right away."
        )

    # ── API key storage ───────────────────────────────────────────────
    if tool_name in ("lucy_store_api_key", "lucy_custom_store_api_key"):
        service = (parameters.get("service_slug") or "the service").replace("_", " ").title()
        return (
            f"I'll store your *{service}* API key so I can use it for all future requests. "
            "It'll be kept securely and never exposed in messages."
        )

    # ── Email sending ─────────────────────────────────────────────────
    if any(kw in tool_name.lower() for kw in ("send_email", "send_message", "gmail_send")):
        to = parameters.get("recipient_email") or parameters.get("to", "")
        subject = parameters.get("subject", "")
        parts: list[str] = []
        if to:
            parts.append(f"To: *{to}*")
        if subject:
            parts.append(f"Subject: _{subject}_")
        detail = "  ·  ".join(parts) if parts else "an email"
        return f"I'm about to send {detail}. Approve to send it."

    # ── Calendar event creation ───────────────────────────────────────
    if any(kw in tool_name.lower() for kw in ("create_event", "create_calendar")):
        title = parameters.get("title") or parameters.get("summary", "")
        when = parameters.get("start_datetime") or parameters.get("start", "")
        desc = f"*{title}*" if title else "a new event"
        if when:
            desc += f" at {when}"
        return f"I need your confirmation to add {desc} to your calendar."

    # ── Cron / heartbeat creation ─────────────────────────────────────
    if "create_cron" in tool_name.lower():
        title = parameters.get("title") or parameters.get("name", "")
        detail = f" — *{title}*" if title else ""
        return f"I'll set up a scheduled task{detail}. Approve to create it."

    if "create_heartbeat" in tool_name.lower():
        title = parameters.get("title") or parameters.get("name", "")
        detail = f" — *{title}*" if title else ""
        return f"I'll create a heartbeat monitor{detail}."

    # ── Deletions ─────────────────────────────────────────────────────
    if any(kw in tool_name.lower() for kw in ("delete", "remove", "destroy")):
        thing = (
            parameters.get("title")
            or parameters.get("name")
            or parameters.get("user_id")
            or parameters.get("id")
            or "this item"
        )
        return (
            f"I'm about to permanently delete *{thing}*. "
            "This can't be undone — approve only if you're sure."
        )

    # ── Generic fallback ──────────────────────────────────────────────
    stripped = tool_name.removeprefix("lucy_custom_").removeprefix("lucy_")
    readable = _humanize_tool_name(stripped)
    param_summary = _summarize_params(stripped, parameters)
    if param_summary:
        return f"I'd like to *{readable}*:\n{param_summary}"
    return f"I'd like to *{readable}*. Approve to proceed."


def create_gated_result(
    tool_name: str,
    parameters: dict[str, Any],
    action_type: ActionType,
    workspace_id: str,
    requesting_user_id: str = "",
) -> dict[str, Any]:
    """Create the pending action and return a result the agent can use.

    The agent receives this instead of the actual tool result. It tells
    the agent to present an approval prompt to the user with Block Kit
    buttons.

    requesting_user_id: Slack user ID of the person who triggered this agent run.
    Stored with the action so the approval handler can enforce ownership.
    """
    from lucy.slack.hitl import create_pending_action

    description = format_confirmation_message(tool_name, parameters, action_type)

    action_id = create_pending_action(
        tool_name=tool_name,
        parameters=parameters,
        description=description,
        workspace_id=workspace_id,
        requesting_user_id=requesting_user_id,
    )

    logger.info(
        "confirmation_gate_action_gated",
        tool=tool_name,
        action_type=action_type.value,
        action_id=action_id,
        workspace_id=workspace_id,
    )

    severity = "destructive" if action_type == ActionType.DESTRUCTIVE else "write"

    return {
        "status": "pending_approval",
        "action_id": action_id,
        "action_type": action_type.value,
        "severity": severity,
        "description": description,
        "message": (
            "This action requires user confirmation before execution. "
            "Present the approval prompt to the user using the Block Kit "
            "format with Approve and Cancel buttons. Include the action_id "
            "so the HITL system can resolve it.\n\n"
            "IMPORTANT: Use these Block Kit elements:\n"
            "1. A section block with the action description\n"
            "2. An actions block with:\n"
            f'   - Approve button: action_id="lucy_action_approve_{action_id}", '
            f'value="{action_id}"\n'
            f'   - Cancel button: action_id="lucy_action_cancel_{action_id}", '
            f'value="{action_id}"\n\n'
            "Do NOT proceed with the action until the user clicks Approve."
        ),
        "blocks": _build_approval_blocks(action_id, description, severity),
    }


def _build_approval_blocks(
    action_id: str,
    description: str,
    severity: str,
) -> list[dict[str, Any]]:
    """Build Slack Block Kit blocks for the approval prompt.

    Layout:
      [optional warning header for destructive actions]
      [section: natural narrative]
      [divider]
      [actions: Approve | Reject]
    """
    blocks: list[dict[str, Any]] = []

    if severity == "destructive":
        blocks.append({
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": "⚠️  *Heads up — this action cannot be undone*"},
            ],
        })

    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": description},
    })

    blocks.append({"type": "divider"})

    blocks.append({
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Approve", "emoji": False},
                "style": "primary",
                "action_id": f"lucy_action_approve_{action_id}",
                "value": action_id,
            },
            {
                # No "style" = default (light grey) — much better contrast than "danger"
                "type": "button",
                "text": {"type": "plain_text", "text": "Reject", "emoji": False},
                "action_id": f"lucy_action_cancel_{action_id}",
                "value": action_id,
            },
        ],
    })

    return blocks


# ── Helper functions ──────────────────────────────────────────────────

def _humanize_tool_name(name: str) -> str:
    """Convert tool_name_like_this to 'Tool Name Like This'."""
    # Remove common prefixes
    for prefix in ("gmail_", "googlecalendar_", "clerk_", "polarsh_"):
        if name.startswith(prefix):
            service = prefix.rstrip("_").replace("googlecalendar", "Calendar")
            action = name[len(prefix):]
            return f"{service}: {action.replace('_', ' ').title()}"
    return name.replace("_", " ").title()


def _summarize_params(tool_name: str, params: dict[str, Any]) -> str:
    """Create a human-readable summary of tool parameters.

    Tries to extract the most relevant details based on tool type.
    """
    parts: list[str] = []

    # Email-related
    if "recipient_email" in params:
        parts.append(f"To: {params['recipient_email']}")
    if "subject" in params:
        parts.append(f"Subject: {params['subject']}")
    if "body" in params:
        body = str(params["body"])
        if len(body) > 100:
            body = body[:100] + "..."
        parts.append(f"Body: {body}")

    # Calendar-related
    if "title" in params and "recipient_email" not in params:
        parts.append(f"Title: {params['title']}")
    if "start_datetime" in params:
        parts.append(f"When: {params['start_datetime']}")
    if "attendees" in params:
        attendees = params["attendees"]
        if isinstance(attendees, list):
            parts.append(f"With: {', '.join(attendees)}")

    # Identity-related
    if "user_id" in params and params["user_id"] != "me":
        parts.append(f"User: {params['user_id']}")
    if "event_id" in params:
        parts.append(f"Event: {params['event_id']}")

    # Generic fallback for unrecognized params
    if not parts:
        # Show top 3 most informative params
        skip = {"api_key", "token", "secret", "password", "confirmed"}
        shown = 0
        for key, val in params.items():
            if key in skip or shown >= 3:
                continue
            val_str = str(val)
            if len(val_str) > 80:
                val_str = val_str[:80] + "..."
            parts.append(f"{key}: {val_str}")
            shown += 1

    if parts:
        return "Details:\n" + "\n".join(f"  • {p}" for p in parts)
    return ""
