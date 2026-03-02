"""Human-in-the-loop (HITL) action management for Lucy.

Manages pending destructive actions that require user approval before
execution. When the agent detects a destructive action (delete, send,
cancel), it stores the action details here and presents an approval
prompt to the user via Block Kit buttons.

Pending actions expire after PENDING_TTL_SECONDS.

Storage: in-memory (fast path) + workspace file (survive restarts).
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

import structlog

from lucy.config import settings

logger = structlog.get_logger()

PENDING_TTL_SECONDS = 300.0

_pending_actions: dict[str, dict[str, Any]] = {}


def _hitl_store_path(workspace_id: str) -> Path:
    """Return path to the workspace-level HITL state file."""
    root = Path(settings.workspace_root) / workspace_id / "data"
    root.mkdir(parents=True, exist_ok=True)
    return root / "pending_actions.json"


def _persist_action(workspace_id: str, action_id: str, action: dict[str, Any]) -> None:
    """Write a single pending action to disk."""
    try:
        path = _hitl_store_path(workspace_id)
        data: dict[str, Any] = {}
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning("hitl_store_parse_failed", error=str(e))
                data = {}
        # Store wall-clock timestamp for cross-process TTL
        storable = {**action, "wall_created_at": time.time()}
        data[action_id] = storable
        # Prune expired entries while we're here
        cutoff = time.time() - PENDING_TTL_SECONDS
        data = {k: v for k, v in data.items() if v.get("wall_created_at", 0) > cutoff}
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("hitl_persist_failed", error=str(e))


def _remove_persisted_action(workspace_id: str, action_id: str) -> None:
    """Remove a single pending action from disk."""
    try:
        path = _hitl_store_path(workspace_id)
        if not path.exists():
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        data.pop(action_id, None)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("hitl_remove_failed", error=str(e))


def _load_from_disk(workspace_id: str) -> dict[str, dict[str, Any]]:
    """Load surviving HITL actions from disk into in-memory store."""
    try:
        path = _hitl_store_path(workspace_id)
        if not path.exists():
            return {}
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        cutoff = time.time() - PENDING_TTL_SECONDS
        valid = {}
        for k, v in data.items():
            if v.get("wall_created_at", 0) > cutoff:
                # Restore monotonic-compatible created_at (approximate)
                elapsed = time.time() - v.get("wall_created_at", time.time())
                v["created_at"] = time.monotonic() - elapsed
                valid[k] = v
        return valid
    except Exception as e:
        logger.warning("hitl_load_from_disk_failed", error=str(e))
        return {}


def create_pending_action(
    tool_name: str,
    parameters: dict[str, Any],
    description: str,
    workspace_id: str,
    requesting_user_id: str = "",
) -> str:
    """Store a pending action and return its unique ID.

    The action will be held until the user approves or cancels it,
    or until it expires after PENDING_TTL_SECONDS.

    requesting_user_id: Slack user ID of the person who triggered this action.
    When provided, only that user (or a workspace admin) can approve it.
    """
    action_id = uuid.uuid4().hex[:12]
    action = {
        "tool_name": tool_name,
        "parameters": parameters,
        "description": description,
        "workspace_id": workspace_id,
        "requesting_user_id": requesting_user_id,
        "created_at": time.monotonic(),
        "wall_created_at": time.time(),
    }
    _pending_actions[action_id] = action
    _persist_action(workspace_id, action_id, action)

    _cleanup_expired()

    logger.info(
        "hitl_action_created",
        action_id=action_id,
        tool=tool_name,
        workspace_id=workspace_id,
    )
    return action_id


def get_pending_action_metadata(action_id: str) -> dict[str, Any] | None:
    """Return metadata for a pending action without resolving it.

    Used for pre-flight checks (e.g., ownership verification) before approval.
    Returns None if the action is not found in memory (disk not checked — this
    is intentional: a server restart means we can't verify ownership, so we
    allow the approval to proceed rather than block legitimate post-restart use).
    """
    return _pending_actions.get(action_id)


async def resolve_pending_action(
    action_id: str,
    approved: bool,
) -> dict[str, Any] | None:
    """Resolve a pending action (approve or cancel).

    Returns the action data if approved and found, None otherwise.
    Falls back to disk if not in the in-memory store (handles restarts).
    """
    _cleanup_expired()
    action = _pending_actions.pop(action_id, None)

    if not action:
        # Try loading from disk (server may have restarted).
        # IMPORTANT: Do NOT re-insert into _pending_actions here — that would
        # create a ghost entry that a second concurrent resolve call could pop
        # and execute again, causing double-execution in multi-process deploys.
        logger.info("hitl_action_not_in_memory_trying_disk", action_id=action_id)
        root = Path(settings.workspace_root)
        if root.is_dir():
            for ws_dir in root.iterdir():
                if ws_dir.is_dir():
                    disk_actions = _load_from_disk(ws_dir.name)
                    if action_id in disk_actions:
                        action = disk_actions[action_id]
                        logger.info(
                            "hitl_action_recovered_from_disk",
                            action_id=action_id,
                            workspace_id=ws_dir.name,
                        )
                        break

    if not action:
        logger.warning("hitl_action_not_found", action_id=action_id)
        return None

    # Remove from disk regardless of approval/cancel
    workspace_id = action.get("workspace_id", "")
    if workspace_id:
        _remove_persisted_action(workspace_id, action_id)

    if approved:
        logger.info("hitl_action_approved", action_id=action_id)
        return action

    logger.info("hitl_action_cancelled", action_id=action_id)
    return None


_expiry_callbacks: list[Any] = []


def register_expiry_callback(cb: Any) -> None:
    """Register a callback to be called when a pending action expires.

    Callback signature: async def cb(action: dict[str, Any]) -> None
    Used by the Slack handler to notify the requesting user that their
    action timed out and they should re-issue the request if needed.
    """
    _expiry_callbacks.append(cb)


def _cleanup_expired() -> None:
    """Remove expired pending actions and fire expiry callbacks."""
    import asyncio

    now = time.monotonic()
    expired = [
        (aid, data) for aid, data in _pending_actions.items()
        if now - data["created_at"] > PENDING_TTL_SECONDS
    ]
    for aid, data in expired:
        _pending_actions.pop(aid, None)
        # Remove from disk so it doesn't reappear after restart
        workspace_id = data.get("workspace_id", "")
        if workspace_id:
            _remove_persisted_action(workspace_id, aid)

        logger.info(
            "hitl_action_expired",
            action_id=aid,
            tool=data.get("tool_name"),
            requesting_user=data.get("requesting_user_id"),
        )

        # Fire expiry callbacks (e.g. notify the requesting user in Slack)
        for cb in _expiry_callbacks:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(cb(data))
            except Exception as exc:
                logger.debug("hitl_expiry_callback_failed", error=str(exc))


DESTRUCTIVE_ACTION_PATTERNS: frozenset[str] = frozenset({
    "DELETE", "REMOVE", "CANCEL", "SEND", "FORWARD",
    "ARCHIVE", "DESTROY", "REVOKE", "UNSUBSCRIBE",
})


def is_destructive_tool_call(tool_name: str) -> bool:
    """Check if a tool call name indicates a destructive action."""
    upper = tool_name.upper()
    return any(pattern in upper for pattern in DESTRUCTIVE_ACTION_PATTERNS)
