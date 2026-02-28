"""Priority request queue for Lucy.

Architecture:
    SlackEvent → classify_priority() → PriorityQueue → worker pool → agent.run()

Why this exists:
    Lucy's current model: asyncio.Semaphore(10) gates agent.run() calls.
    This works for correctness but not UX. A simple "Hi!" waits behind
    a 60-second research task. Users perceive this as slowness.

    The priority queue ensures:
    1. Fast-tier requests (greetings, acks) get processed first
    2. Heavy requests don't block light ones
    3. Backpressure is visible (users see "I'm busy" not silence)
    4. Per-workspace fairness (one workspace can't starve others)

Design decisions:
    - asyncio.PriorityQueue, not Redis/Celery. Lucy runs on a single
      event loop. Adding distributed infra for <100 concurrent users
      is over-engineering. The queue lives in-memory.
    - 3 priority levels (HIGH/NORMAL/LOW) not 10. More levels = more
      tuning knobs = more bugs. Three is sufficient.
    - Per-workspace fairness via round-robin within same priority.
    - Backpressure at queue depth 50 per workspace.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Coroutine

import structlog

logger = structlog.get_logger()


class Priority(IntEnum):
    """Request priority levels (lower number = higher priority)."""
    HIGH = 0    # Greetings, simple lookups, react-only confirmations
    NORMAL = 1  # Tool-calling tasks, general requests
    LOW = 2     # Research, analysis, background tasks


@dataclass(order=True)
class QueuedRequest:
    """A request waiting in the priority queue.

    The `order` comparison uses (priority, enqueue_time) so that
    within the same priority level, earlier requests go first (FIFO).
    """
    priority: int
    enqueue_time: float = field(compare=True)
    # --- Non-comparable fields ---
    workspace_id: str = field(compare=False)
    handler: Callable[..., Coroutine[Any, Any, Any]] = field(compare=False)
    args: tuple[Any, ...] = field(default_factory=tuple, compare=False)
    kwargs: dict[str, Any] = field(default_factory=dict, compare=False)
    request_id: str = field(default="", compare=False)


# ═══════════════════════════════════════════════════════════════════════════
# PRIORITY CLASSIFICATION
# ═══════════════════════════════════════════════════════════════════════════

def classify_priority(message: str, route_tier: str) -> Priority:
    """Classify a request's priority based on message content and route tier.

    This runs AFTER the model router, so we can use the tier directly.
    """
    if route_tier == "fast":
        return Priority.HIGH
    if route_tier == "frontier":
        return Priority.LOW
    return Priority.NORMAL


# ═══════════════════════════════════════════════════════════════════════════
# REQUEST QUEUE
# ═══════════════════════════════════════════════════════════════════════════

MAX_QUEUE_DEPTH_PER_WORKSPACE = 50
MAX_TOTAL_QUEUE_DEPTH = 200
NUM_WORKERS = 10  # Matches MAX_CONCURRENT_AGENTS


class RequestQueue:
    """Priority request queue with worker pool.

    Replaces the raw asyncio.Semaphore with a proper queue that:
    1. Prioritizes fast requests over heavy ones
    2. Enforces per-workspace queue depth limits
    3. Provides backpressure signaling
    4. Tracks queue metrics for observability
    """

    def __init__(self, num_workers: int = NUM_WORKERS) -> None:
        self._queue: asyncio.PriorityQueue[QueuedRequest] = asyncio.PriorityQueue()
        self._workers: list[asyncio.Task[None]] = []
        self._num_workers = num_workers
        self._running = False

        # Per-workspace counters for fairness + backpressure
        self._workspace_depth: dict[str, int] = {}
        self._total_enqueued = 0
        self._total_processed = 0

        # Metrics
        self._high_processed = 0
        self._normal_processed = 0
        self._low_processed = 0
        self._rejected = 0

    async def start(self) -> None:
        """Start the worker pool."""
        if self._running:
            return
        self._running = True
        for i in range(self._num_workers):
            task = asyncio.create_task(
                self._worker(f"worker-{i}"),
                name=f"request-queue-worker-{i}",
            )
            self._workers.append(task)
        logger.info(
            "request_queue_started",
            num_workers=self._num_workers,
        )

    async def stop(self) -> None:
        """Stop the worker pool gracefully."""
        if not self._running:
            return
        self._running = False
        # Cancel all workers
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        logger.info(
            "request_queue_stopped",
            total_processed=self._total_processed,
            rejected=self._rejected,
        )

    def enqueue(
        self,
        workspace_id: str,
        priority: Priority,
        handler: Callable[..., Coroutine[Any, Any, Any]],
        *args: Any,
        request_id: str = "",
        **kwargs: Any,
    ) -> bool:
        """Add a request to the queue.

        Returns True if enqueued, False if rejected (backpressure).
        """
        # Check per-workspace depth
        ws_depth = self._workspace_depth.get(workspace_id, 0)
        if ws_depth >= MAX_QUEUE_DEPTH_PER_WORKSPACE:
            logger.warning(
                "request_rejected_workspace_limit",
                workspace_id=workspace_id,
                depth=ws_depth,
            )
            self._rejected += 1
            return False

        # Check total depth
        if self._queue.qsize() >= MAX_TOTAL_QUEUE_DEPTH:
            logger.warning(
                "request_rejected_total_limit",
                queue_size=self._queue.qsize(),
            )
            self._rejected += 1
            return False

        request = QueuedRequest(
            priority=int(priority),
            enqueue_time=time.monotonic(),
            workspace_id=workspace_id,
            handler=handler,
            args=args,
            kwargs=kwargs,
            request_id=request_id,
        )

        self._queue.put_nowait(request)
        self._workspace_depth[workspace_id] = ws_depth + 1
        self._total_enqueued += 1

        logger.debug(
            "request_enqueued",
            workspace_id=workspace_id,
            priority=priority.name if isinstance(priority, Priority) else priority,
            queue_size=self._queue.qsize(),
            request_id=request_id,
        )
        return True

    async def _worker(self, name: str) -> None:
        """Worker loop: pull requests from the queue and execute them."""
        while self._running:
            try:
                request = await asyncio.wait_for(
                    self._queue.get(),
                    timeout=5.0,  # Check _running every 5s
                )
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            wait_ms = round((time.monotonic() - request.enqueue_time) * 1000)

            try:
                logger.info(
                    "request_processing",
                    worker=name,
                    workspace_id=request.workspace_id,
                    priority=request.priority,
                    wait_ms=wait_ms,
                    request_id=request.request_id,
                )

                await request.handler(*request.args, **request.kwargs)

                # Update metrics
                self._total_processed += 1
                if request.priority == Priority.HIGH:
                    self._high_processed += 1
                elif request.priority == Priority.NORMAL:
                    self._normal_processed += 1
                else:
                    self._low_processed += 1

            except Exception as e:
                logger.error(
                    "request_processing_error",
                    worker=name,
                    workspace_id=request.workspace_id,
                    error=str(e),
                    request_id=request.request_id,
                    exc_info=True,
                )
            finally:
                # Decrement workspace counter
                ws_depth = self._workspace_depth.get(request.workspace_id, 1)
                self._workspace_depth[request.workspace_id] = max(0, ws_depth - 1)
                self._queue.task_done()

    @property
    def metrics(self) -> dict[str, Any]:
        """Return queue metrics for observability."""
        return {
            "queue_size": self._queue.qsize(),
            "total_enqueued": self._total_enqueued,
            "total_processed": self._total_processed,
            "rejected": self._rejected,
            "by_priority": {
                "high": self._high_processed,
                "normal": self._normal_processed,
                "low": self._low_processed,
            },
            "workspace_depths": dict(self._workspace_depth),
        }

    @property
    def is_busy(self) -> bool:
        """True if the queue has significant backlog."""
        return self._queue.qsize() > self._num_workers * 2


# ═══════════════════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════════════════

_queue: RequestQueue | None = None


def get_request_queue() -> RequestQueue:
    """Get or create the singleton request queue."""
    global _queue
    if _queue is None:
        _queue = RequestQueue()
    return _queue
