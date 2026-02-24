"""Edge case handlers for concurrent/background scenarios.

Handles the tricky cases that emerge when multiple features interact:
1. Thread interrupts during background tasks
2. User asking about task status
3. Concurrent tool calls to the same external API
4. Graceful degradation when dependencies fail

These are wired into handlers.py as middleware checks.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger()


# ═══════════════════════════════════════════════════════════════════════════
# TASK STATUS QUERIES — "What are you working on?"
# ═══════════════════════════════════════════════════════════════════════════

_STATUS_PATTERNS = re.compile(
    r"(?i)\b(?:"
    r"what (?:are you|r u) (?:working on|doing|up to)"
    r"|(?:are you|r u) (?:busy|available|free|idle)"
    r"|(?:how(?:'s| is) (?:that|the) (?:going|coming|progressing))"
    r"|(?:any )?(?:update|progress|status)(?: on)?"
    r"|(?:still )?(?:working on|processing)"
    r"|(?:is (?:that|it) (?:done|ready|finished))"
    r")\b"
)

_TASK_REFERENCE_PATTERNS = re.compile(
    r"(?i)\b(?:"
    r"(?:cancel|stop|abort|kill) (?:that|it|the (?:task|research|analysis))"
    r"|(?:nevermind|never\s*mind)"
    r"|(?:don'?t|dont) (?:bother|worry about)"
    r"|(?:scratch|forget) (?:that|it)"
    r")\b"
)


def is_status_query(message: str) -> bool:
    """Check if the message is asking about current task status."""
    return bool(_STATUS_PATTERNS.search(message))


def is_task_cancellation(message: str) -> bool:
    """Check if the message is requesting task cancellation."""
    return bool(_TASK_REFERENCE_PATTERNS.search(message))


async def format_task_status(
    workspace_id: str,
) -> str | None:
    """Format current background task status for a workspace.

    Returns a human-friendly status string, or None if no tasks.
    """
    try:
        from lucy.core.task_manager import get_task_manager, TaskState
        tm = get_task_manager()
        tasks = tm.get_workspace_tasks(workspace_id)

        active = [
            t for t in tasks
            if t.state in (TaskState.PENDING, TaskState.ACKNOWLEDGED, TaskState.WORKING)
        ]

        if not active:
            return None

        lines = []
        for task in active:
            elapsed = time.monotonic() - task.started_at if task.started_at else 0
            elapsed_str = f"{int(elapsed)}s" if elapsed > 0 else "just started"
            lines.append(f"• *{task.description[:80]}* — {task.state.value} ({elapsed_str})")

        return "\n".join(lines)

    except ImportError:
        return None
    except Exception as e:
        logger.warning("task_status_format_failed", error=str(e))
        return None


async def handle_task_cancellation(
    workspace_id: str,
    thread_ts: str | None = None,
) -> str | None:
    """Cancel the most recent background task for a workspace/thread.

    Returns a confirmation message, or None if no task found.
    """
    try:
        from lucy.core.task_manager import get_task_manager, TaskState
        tm = get_task_manager()
        tasks = tm.get_workspace_tasks(workspace_id)

        active = [
            t for t in tasks
            if t.state in (TaskState.PENDING, TaskState.ACKNOWLEDGED, TaskState.WORKING)
        ]

        if not active:
            return None

        # Cancel the most recent one (or match by thread)
        target = active[-1]  # Most recent
        if thread_ts:
            for t in active:
                if getattr(t, 'thread_ts', None) == thread_ts:
                    target = t
                    break

        await tm.cancel_task(target.task_id)
        return f"Cancelled: *{target.description[:80]}*"

    except ImportError:
        return None
    except Exception as e:
        logger.warning("task_cancellation_failed", error=str(e))
        return None


# ═══════════════════════════════════════════════════════════════════════════
# THREAD INTERRUPT HANDLING
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class InterruptDecision:
    """What to do when a new message arrives in a thread with active work."""

    action: str  # "respond_independently", "queue", "ignore"
    reason: str


def decide_thread_interrupt(
    message: str,
    has_active_bg_task: bool,
    thread_depth: int = 0,
) -> InterruptDecision:
    """Decide how to handle a message that arrives while a bg task runs.

    Rules:
    1. Status queries → format task status (no new agent run)
    2. Cancellation requests → cancel task (no new agent run)
    3. Short/simple messages → respond independently (agent run)
    4. Complex new requests → respond independently (separate context)
    """
    if not has_active_bg_task:
        return InterruptDecision(
            action="respond_independently",
            reason="no_active_task",
        )

    if is_status_query(message):
        return InterruptDecision(
            action="status_reply",
            reason="status_query_during_bg_task",
        )

    if is_task_cancellation(message):
        return InterruptDecision(
            action="cancel_task",
            reason="cancellation_request",
        )

    # New message while bg task runs → handle independently
    return InterruptDecision(
        action="respond_independently",
        reason="new_message_during_bg_task",
    )


# ═══════════════════════════════════════════════════════════════════════════
# CONCURRENT API CALL PROTECTION
# ═══════════════════════════════════════════════════════════════════════════

_IDEMPOTENT_ACTIONS = frozenset({
    "get", "list", "search", "find", "fetch", "read", "check", "query",
})

_MUTATING_ACTIONS = frozenset({
    "create", "update", "delete", "send", "post", "remove", "cancel",
    "merge", "deploy", "schedule",
})


def classify_tool_idempotency(tool_name: str) -> str:
    """Classify whether a tool call is safe to retry/duplicate.

    Returns: "idempotent", "mutating", or "unknown".
    """
    name_lower = tool_name.lower()

    for action in _IDEMPOTENT_ACTIONS:
        if action in name_lower:
            return "idempotent"

    for action in _MUTATING_ACTIONS:
        if action in name_lower:
            return "mutating"

    return "unknown"


def should_deduplicate_tool_call(
    tool_name: str,
    parameters: dict[str, Any],
    recent_calls: list[tuple[str, dict[str, Any], float]],
    window_seconds: float = 5.0,
) -> bool:
    """Check if this tool call is a duplicate of a recent one.

    Only deduplicates mutating calls — two identical GETs are fine.
    Two identical CREATEs within 5 seconds are probably a bug.

    Args:
        tool_name: Tool being called.
        parameters: Call parameters.
        recent_calls: List of (tool_name, params, timestamp) recent calls.
        window_seconds: Dedup window.

    Returns:
        True if this call should be skipped (it's a duplicate).
    """
    if classify_tool_idempotency(tool_name) == "idempotent":
        return False

    now = time.monotonic()
    for prev_name, prev_params, prev_ts in recent_calls:
        if now - prev_ts > window_seconds:
            continue
        if prev_name == tool_name and prev_params == parameters:
            logger.warning(
                "duplicate_mutating_call_blocked",
                tool=tool_name,
                window=window_seconds,
            )
            return True

    return False


# ═══════════════════════════════════════════════════════════════════════════
# GRACEFUL DEGRADATION
# ═══════════════════════════════════════════════════════════════════════════

_ERROR_TYPE_TO_POOL: dict[str, str] = {
    "rate_limited": "error_rate_limit",
    "tool_timeout": "error_timeout",
    "service_unavailable": "error_connection",
    "context_overflow": "error_generic",
}


def get_degradation_message(error_type: str) -> str:
    """Get a user-friendly degradation message for an error type.

    Draws from LLM-generated message pools (pre-warmed at startup).
    Never exposes internal details — just warm, actionable framing.
    """
    from lucy.core.humanize import pick

    category = _ERROR_TYPE_TO_POOL.get(error_type, "error_generic")
    return pick(category)


def classify_error_for_degradation(error: Exception) -> str:
    """Classify an exception into a degradation category."""
    error_str = str(error).lower()

    if "429" in error_str or "rate limit" in error_str:
        return "rate_limited"
    if "timeout" in error_str or "timed out" in error_str:
        return "tool_timeout"
    if any(code in error_str for code in ("502", "503", "504", "unavailable")):
        return "service_unavailable"
    if "context" in error_str and ("length" in error_str or "token" in error_str):
        return "context_overflow"

    return "unknown"
