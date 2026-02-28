"""Slack integration: handlers, middleware, Block Kit, human-in-the-loop."""

from lucy.slack.blockkit import approval_blocks, text_to_blocks
from lucy.slack.handlers import register_handlers
from lucy.slack.hitl import create_pending_action, is_destructive_tool_call
from lucy.slack.middleware import (
    resolve_channel_middleware,
    resolve_user_middleware,
    resolve_workspace_middleware,
)

__all__ = [
    "approval_blocks",
    "create_pending_action",
    "is_destructive_tool_call",
    "register_handlers",
    "resolve_channel_middleware",
    "resolve_user_middleware",
    "resolve_workspace_middleware",
    "text_to_blocks",
]
