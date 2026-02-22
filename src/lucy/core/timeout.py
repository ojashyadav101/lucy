"""Per-tool timeout budget enforcement for Lucy.

Different tool types have different latency budgets. This module provides
a single ``with_timeout`` helper that wraps any async callable and returns
a structured error dict (compatible with ``_execute_tools`` result format)
instead of letting a slow external call block the entire task indefinitely.

Budget table
------------
  COMPOSIO_META_TOOL     45 s   (session-based orchestration calls)
  INTEGRATION_TOOL       20 s   (individual API calls: calendar, github, …)
  LLM_CALL               90 s   (LLM router round-trip)
  DEFAULT                30 s   (anything not explicitly classified)

Usage
-----
    from lucy.core.timeout import with_timeout, ToolType

    result = await with_timeout(
        my_async_fn(arg1, arg2),
        tool_type=ToolType.INTEGRATION_TOOL,
        tool_name="GOOGLECALENDAR_EVENTS_LIST",
    )
    # result is either the real return value or a structured timeout error dict
"""

from __future__ import annotations

import asyncio
from enum import Enum
from typing import Any, Coroutine, TypeVar

import structlog

logger = structlog.get_logger()

T = TypeVar("T")


class ToolType(Enum):
    COMPOSIO_META_TOOL = "composio_meta"
    INTEGRATION_TOOL = "integration"
    LLM_CALL = "llm"
    DEFAULT = "default"


# Timeout budgets in seconds, keyed by ToolType
_BUDGETS: dict[ToolType, float] = {
    ToolType.COMPOSIO_META_TOOL: 45.0,
    ToolType.INTEGRATION_TOOL: 20.0,
    ToolType.LLM_CALL: 90.0,
    ToolType.DEFAULT: 30.0,
}


def budget_for(tool_type: ToolType) -> float:
    """Return the timeout budget in seconds for a given tool type."""
    return _BUDGETS[tool_type]


def classify_tool(tool_name: str) -> ToolType:
    """Classify a tool name into a ToolType for budget lookup."""
    upper = tool_name.upper()
    if upper.startswith("COMPOSIO_"):
        return ToolType.COMPOSIO_META_TOOL
    if any(upper.startswith(p) for p in (
        "GOOGLECALENDAR_", "GMAIL_", "GOOGLEDRIVE_", "GOOGLEDOCS_",
        "GOOGLESHEETS_", "GITHUB_", "LINEAR_", "NOTION_", "SLACK_",
        "JIRA_", "TRELLO_", "FIGMA_", "ASANA_",
    )):
        return ToolType.INTEGRATION_TOOL
    return ToolType.DEFAULT


async def with_timeout(
    coro: Coroutine[Any, Any, T],
    tool_type: ToolType = ToolType.DEFAULT,
    tool_name: str = "unknown",
    override_seconds: float | None = None,
) -> T | dict[str, Any]:
    """Execute *coro* with a per-tool-type timeout budget.

    Args:
        coro: Awaitable to execute.
        tool_type: Used to look up the timeout budget.
        tool_name: Tool name (for logging only).
        override_seconds: If provided, overrides the budget from the table.

    Returns:
        The awaited result of *coro*, or a structured error dict on timeout.

    The error dict is compatible with the ``_execute_tools`` result format:
        {
            "tool": tool_name,
            "status": "error",
            "error_type": "retryable",
            "error": "Tool call timed out after Xs. …",
        }
    """
    seconds = override_seconds if override_seconds is not None else budget_for(tool_type)
    try:
        return await asyncio.wait_for(coro, timeout=seconds)
    except asyncio.TimeoutError:
        logger.warning(
            "tool_timeout",
            tool=tool_name,
            tool_type=tool_type.value,
            budget_s=seconds,
        )
        return {
            "tool": tool_name,
            "status": "error",
            "error_type": "retryable",
            "error": (
                f"Tool call timed out after {seconds:.0f}s. "
                "The external service may be slow. Please try again."
            ),
        }
