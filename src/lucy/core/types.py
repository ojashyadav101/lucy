"""Shared types for the Lucy core module.

Kept in a separate file to avoid circular imports between agent.py and memory/sync.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from lucy.db.models import Task, Workspace, User


@dataclass
class TaskContext:
    """Context for executing a task."""

    task: Task
    workspace: Workspace
    requester: User | None
    session_id: str | None = None
    slack_channel_id: str | None = None
    slack_thread_ts: str | None = None
