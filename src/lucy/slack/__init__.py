"""Slack integration module for Lucy.

Exports:
- middleware: Workspace and user resolution
- handlers: Event handlers for mentions, messages, commands
- blocks: Block Kit message composition
"""

from lucy.slack.middleware import resolve_workspace_middleware, resolve_user_middleware
from lucy.slack.handlers import register_handlers

__all__ = [
    "resolve_workspace_middleware",
    "resolve_user_middleware",
    "register_handlers",
]
