"""Human-in-the-loop (HITL) action management for Lucy.

Manages pending destructive actions that require user approval before
execution. When the agent detects a destructive action (delete, send,
cancel), it stores the action details here and presents an approval
prompt to the user via Block Kit buttons.

Pending actions expire after PENDING_TTL_SECONDS.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import structlog

logger = structlog.get_logger()

PENDING_TTL_SECONDS = 300.0

_pending_actions: dict[str, dict[str, Any]] = {}


def create_pending_action(
    tool_name: str,
    parameters: dict[str, Any],
    description: str,
    workspace_id: str,
) -> str:
    """Store a pending action and return its unique ID.

    The action will be held until the user approves or cancels it,
    or until it expires after PENDING_TTL_SECONDS.
    """
    action_id = uuid.uuid4().hex[:12]
    _pending_actions[action_id] = {
        "tool_name": tool_name,
        "parameters": parameters,
        "description": description,
        "workspace_id": workspace_id,
        "created_at": time.monotonic(),
    }

    _cleanup_expired()

    logger.info(
        "hitl_action_created",
        action_id=action_id,
        tool=tool_name,
        workspace_id=workspace_id,
    )
    return action_id


async def resolve_pending_action(
    action_id: str,
    approved: bool,
) -> dict[str, Any] | None:
    """Resolve a pending action (approve or cancel).

    Returns the action data if approved and found, None otherwise.
    """
    _cleanup_expired()
    action = _pending_actions.pop(action_id, None)

    if not action:
        logger.warning("hitl_action_not_found", action_id=action_id)
        return None

    if approved:
        logger.info("hitl_action_approved", action_id=action_id)
        return action

    logger.info("hitl_action_cancelled", action_id=action_id)
    return None


def get_pending_action(action_id: str) -> dict[str, Any] | None:
    """Retrieve a pending action without resolving it."""
    _cleanup_expired()
    return _pending_actions.get(action_id)


def _cleanup_expired() -> None:
    """Remove expired pending actions."""
    now = time.monotonic()
    expired = [
        aid for aid, data in _pending_actions.items()
        if now - data["created_at"] > PENDING_TTL_SECONDS
    ]
    for aid in expired:
        _pending_actions.pop(aid, None)
        logger.debug("hitl_action_expired", action_id=aid)


DESTRUCTIVE_ACTION_PATTERNS: frozenset[str] = frozenset({
    "DELETE", "REMOVE", "CANCEL", "SEND", "FORWARD",
    "ARCHIVE", "DESTROY", "REVOKE", "UNSUBSCRIBE",
})


def is_destructive_tool_call(tool_name: str) -> bool:
    """Check if a tool call name indicates a destructive action."""
    upper = tool_name.upper()
    return any(pattern in upper for pattern in DESTRUCTIVE_ACTION_PATTERNS)
