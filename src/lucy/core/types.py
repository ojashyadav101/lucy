"""Shared types for the Lucy core module."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AgentContext:
    """Lightweight context for an agent run.

    Re-exported from agent.py for convenience. Use the one in agent.py
    as the canonical definition.
    """

    workspace_id: str
    channel_id: str | None = None
    thread_ts: str | None = None
    user_name: str | None = None
