"""Async task manager for long-running background work.

The problem:
    When Lucy does a 10+ turn research task, the user is stuck waiting.
    They can't ask another question. They can't check on progress.
    The thread is "locked" until the agent finishes.

The solution:
    Long tasks run as background asyncio.Tasks. The user's thread stays
    responsive — they can send new messages, ask for status, or even
    cancel the task. The task posts progress updates to the thread.

Architecture:
    User: "Research competitor pricing and create a report"
    Lucy: "On it — I'll research this in the background and post updates."
    [Background task starts]
    [User can send other messages — handled by fast path or new agent run]
    [Task posts: "Making progress — analyzed 3 competitors so far..."]
    [Task completes: "Here's the full competitive analysis: [report]"]

State machine:
    PENDING → ACKNOWLEDGED → WORKING → [PROGRESS_UPDATE]* → COMPLETED
                                    → FAILED
                                    → CANCELLED

Integration:
    The task manager is NOT the agent loop itself. It WRAPS the agent
    loop. The key insight is that agent.run() is already designed to
    handle multi-turn tool calls. The task manager just:
    1. Runs it in a background asyncio.Task
    2. Manages the lifecycle (start, progress, complete, cancel)
    3. Keeps the Slack thread responsive during execution
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine

import structlog

logger = structlog.get_logger()


class TaskState(str, Enum):
    PENDING = "pending"
    ACKNOWLEDGED = "acknowledged"
    WORKING = "working"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class BackgroundTask:
    """A long-running task with lifecycle management."""
    task_id: str
    workspace_id: str
    channel_id: str
    thread_ts: str
    description: str
    state: TaskState = TaskState.PENDING
    started_at: float = field(default_factory=time.monotonic)
    completed_at: float | None = None
    progress_message_ts: str | None = None  # Message to update with progress
    result: str | None = None
    error: str | None = None
    _asyncio_task: asyncio.Task[Any] | None = field(default=None, repr=False)


# ═══════════════════════════════════════════════════════════════════════════
# TASK CLASSIFICATION — Should this be a background task?
# ═══════════════════════════════════════════════════════════════════════════

import re

_HEAVY_COMPOUND_RE = re.compile(
    r"(?:"
    r"comprehensive\s+(?:research|report|analysis|audit)"
    r"|deep\s+dive"
    r"|thorough\s+(?:analysis|investigation|review)"
    r"|(?:research|analyze|investigate).*(?:and|then|also|plus).*(?:create|write|build|generate)"
    r"|competitive\s+analysis"
    r"|full\s+audit"
    r")",
    re.IGNORECASE,
)


def should_run_as_background_task(
    message: str,
    route_tier: str,
) -> bool:
    """Determine if a request should run as a background task.

    Only frontier-tier tasks with COMPOUND heavy signals qualify.
    Simple "research X" or "compare A vs B" should run synchronously —
    they typically finish in under 60 seconds and backgrounding them
    adds unnecessary UX overhead (ack message + progress updates).
    """
    if route_tier != "frontier":
        return False
    return bool(_HEAVY_COMPOUND_RE.search(message))


# ═══════════════════════════════════════════════════════════════════════════
# TASK MANAGER
# ═══════════════════════════════════════════════════════════════════════════

MAX_BACKGROUND_TASKS = 5  # Per workspace
MAX_TASK_DURATION = 14_400  # 4-hour safety net (supervisor governs real duration)


class TaskManager:
    """Manages background tasks across all workspaces.

    Usage:
        manager = get_task_manager()

        # Start a background task
        task = await manager.start_task(
            workspace_id="W123",
            channel_id="C456",
            thread_ts="1234.5678",
            description="Competitive analysis",
            handler=run_agent_in_background,
        )

        # Check task status
        status = manager.get_task(task.task_id)

        # Cancel a task
        await manager.cancel_task(task.task_id)
    """

    def __init__(self) -> None:
        self._tasks: dict[str, BackgroundTask] = {}
        # Per-workspace task count for limits
        self._workspace_task_count: dict[str, int] = {}

    async def start_task(
        self,
        workspace_id: str,
        channel_id: str,
        thread_ts: str,
        description: str,
        handler: Callable[..., Coroutine[Any, Any, str]],
        slack_client: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> BackgroundTask:
        """Start a new background task.

        Args:
            workspace_id: Workspace this task belongs to
            channel_id: Slack channel to post updates to
            thread_ts: Thread to post updates in
            description: Human-readable description
            handler: Async function to run (should return final response text)
            slack_client: Slack client for posting updates

        Returns:
            BackgroundTask with lifecycle tracking
        """
        # Check workspace limit
        ws_count = self._workspace_task_count.get(workspace_id, 0)
        if ws_count >= MAX_BACKGROUND_TASKS:
            raise RuntimeError(
                f"Workspace {workspace_id} already has {ws_count} "
                f"background tasks running. Max is {MAX_BACKGROUND_TASKS}."
            )

        task_id = f"task_{uuid.uuid4().hex[:12]}"
        task = BackgroundTask(
            task_id=task_id,
            workspace_id=workspace_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            description=description,
        )

        if slack_client:
            try:
                from lucy.pipeline.humanize import pick
                ack_result = await slack_client.chat_postMessage(
                    channel=channel_id,
                    thread_ts=thread_ts,
                    text=pick("task_background_ack"),
                )
                task.progress_message_ts = ack_result.get("ts")
                task.state = TaskState.ACKNOWLEDGED
            except Exception as e:
                logger.warning("task_ack_failed", error=str(e))

        # Start the actual work
        async def _run_and_cleanup() -> None:
            try:
                task.state = TaskState.WORKING

                # Run with timeout
                result = await asyncio.wait_for(
                    handler(*args, **kwargs),
                    timeout=MAX_TASK_DURATION,
                )

                task.result = result
                task.state = TaskState.COMPLETED
                task.completed_at = time.monotonic()

                # Post final result
                if slack_client and result:
                    try:
                        await slack_client.chat_postMessage(
                            channel=channel_id,
                            thread_ts=thread_ts,
                            text=result,
                        )
                    except Exception:
                        pass

                elapsed = round(task.completed_at - task.started_at, 1)
                logger.info(
                    "background_task_completed",
                    task_id=task_id,
                    workspace_id=workspace_id,
                    elapsed_s=elapsed,
                )

            except asyncio.TimeoutError:
                task.state = TaskState.FAILED
                elapsed_h = round(MAX_TASK_DURATION / 3600, 1)
                task.error = f"Task hit {elapsed_h}h safety limit"
                task.completed_at = time.monotonic()
                if slack_client:
                    try:
                        await slack_client.chat_postMessage(
                            channel=channel_id,
                            thread_ts=thread_ts,
                            text=(
                                "This task ran for an unusually long time "
                                "and was stopped as a safety measure. "
                                "Let me know if you'd like me to continue."
                            ),
                        )
                    except Exception:
                        pass
                logger.critical(
                    "background_task_safety_net",
                    task_id=task_id,
                    workspace_id=workspace_id,
                    duration_limit_s=MAX_TASK_DURATION,
                )

            except asyncio.CancelledError:
                task.state = TaskState.CANCELLED
                task.completed_at = time.monotonic()
                logger.info(
                    "background_task_cancelled",
                    task_id=task_id,
                )

            except Exception as e:
                task.state = TaskState.FAILED
                task.error = str(e)
                task.completed_at = time.monotonic()
                if slack_client:
                    try:
                        from lucy.pipeline.humanize import pick
                        await slack_client.chat_postMessage(
                            channel=channel_id,
                            thread_ts=thread_ts,
                            text=pick("error_task_failed"),
                        )
                    except Exception:
                        pass
                logger.error(
                    "background_task_failed",
                    task_id=task_id,
                    error=str(e),
                    exc_info=True,
                )

            finally:
                # Decrement workspace counter
                ws_count = self._workspace_task_count.get(workspace_id, 1)
                self._workspace_task_count[workspace_id] = max(0, ws_count - 1)
                # Clean up old completed tasks (keep last 20)
                self._cleanup_old_tasks()

        asyncio_task = asyncio.create_task(
            _run_and_cleanup(),
            name=f"bg-task-{task_id}",
        )
        task._asyncio_task = asyncio_task

        self._tasks[task_id] = task
        self._workspace_task_count[workspace_id] = ws_count + 1

        logger.info(
            "background_task_started",
            task_id=task_id,
            workspace_id=workspace_id,
            description=description[:100],
        )

        return task

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a running background task."""
        task = self._tasks.get(task_id)
        if not task or task.state not in (
            TaskState.PENDING, TaskState.ACKNOWLEDGED, TaskState.WORKING
        ):
            return False

        if task._asyncio_task and not task._asyncio_task.done():
            task._asyncio_task.cancel()
            return True

        return False

    def get_task(self, task_id: str) -> BackgroundTask | None:
        """Get a task by ID."""
        return self._tasks.get(task_id)

    def get_active_for_thread(
        self,
        thread_ts: str | None,
    ) -> BackgroundTask | None:
        """Return the active background task running in a given thread."""
        if not thread_ts:
            return None
        for t in self._tasks.values():
            if (
                t.thread_ts == thread_ts
                and t.state
                in (TaskState.PENDING, TaskState.ACKNOWLEDGED, TaskState.WORKING)
            ):
                return t
        return None

    def get_workspace_tasks(
        self,
        workspace_id: str,
        active_only: bool = True,
    ) -> list[BackgroundTask]:
        """Get all tasks for a workspace."""
        tasks = [
            t for t in self._tasks.values()
            if t.workspace_id == workspace_id
        ]
        if active_only:
            tasks = [
                t for t in tasks
                if t.state in (
                    TaskState.PENDING,
                    TaskState.ACKNOWLEDGED,
                    TaskState.WORKING,
                )
            ]
        return tasks

    def _cleanup_old_tasks(self) -> None:
        """Remove completed/failed tasks older than 20."""
        completed = sorted(
            [
                (tid, t) for tid, t in self._tasks.items()
                if t.state in (
                    TaskState.COMPLETED,
                    TaskState.FAILED,
                    TaskState.CANCELLED,
                )
            ],
            key=lambda x: x[1].completed_at or 0,
        )
        # Keep last 20 completed tasks
        for tid, _ in completed[:-20]:
            del self._tasks[tid]

    @property
    def metrics(self) -> dict[str, Any]:
        """Return task manager metrics."""
        states = {}
        for task in self._tasks.values():
            states[task.state.value] = states.get(task.state.value, 0) + 1
        return {
            "total_tasks": len(self._tasks),
            "by_state": states,
            "workspace_counts": dict(self._workspace_task_count),
        }


# ═══════════════════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════════════════

_manager: TaskManager | None = None


def get_task_manager() -> TaskManager:
    """Get or create the singleton task manager."""
    global _manager
    if _manager is None:
        _manager = TaskManager()
    return _manager
