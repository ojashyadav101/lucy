"""Lucy Phase 1 Metrics Instrumentation.

In-process, zero-dependency metrics collector tracking:
  - Reliability counters (tool loops, unknown tools, no-text fallbacks, errors)
  - Latency histograms for tools, LLM, and end-to-end task execution
  - Task throughput by status

All state is held in a single process-global singleton. No external server
(Prometheus, StatsD, etc.) is required — snapshots are exported as plain dicts
on /metrics and emitted as structured log lines after every task.

Thread-safety: counters/histograms use an asyncio.Lock so they are safe from
concurrent coroutines. Sync contexts (tests) can call the _sync_* helpers
directly without holding the loop.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections import defaultdict
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Iterator


# ---------------------------------------------------------------------------
# Histogram implementation
# ---------------------------------------------------------------------------

# Fixed upper-bound buckets in milliseconds.
_LATENCY_BUCKETS_MS: tuple[float, ...] = (
    5, 10, 25, 50, 100, 200, 500, 1_000, 2_000, 5_000,
    10_000, 20_000, 30_000, 60_000, float("inf"),
)


@dataclass
class Histogram:
    """Lightweight latency histogram backed by fixed buckets + running stats."""

    name: str
    _buckets: list[int] = field(default_factory=lambda: [0] * len(_LATENCY_BUCKETS_MS))
    _count: int = 0
    _sum_ms: float = 0.0
    _min_ms: float = float("inf")
    _max_ms: float = 0.0

    def record(self, value_ms: float) -> None:
        self._count += 1
        self._sum_ms += value_ms
        self._min_ms = min(self._min_ms, value_ms)
        self._max_ms = max(self._max_ms, value_ms)
        for i, bound in enumerate(_LATENCY_BUCKETS_MS):
            if value_ms <= bound:
                self._buckets[i] += 1
                break

    @property
    def count(self) -> int:
        return self._count

    @property
    def mean_ms(self) -> float:
        return self._sum_ms / self._count if self._count else 0.0

    def percentile(self, p: float) -> float:
        """Estimate percentile via linear interpolation across buckets."""
        if self._count == 0:
            return 0.0
        target = math.ceil(p / 100 * self._count)
        cumulative = 0
        prev_bound = 0.0
        for i, bound in enumerate(_LATENCY_BUCKETS_MS):
            cumulative += self._buckets[i]
            if cumulative >= target:
                # Linear interpolation between previous and current bound
                bucket_count = self._buckets[i]
                if bucket_count == 0:
                    return bound
                frac = (target - (cumulative - bucket_count)) / bucket_count
                upper = bound if not math.isinf(bound) else self._max_ms
                return prev_bound + frac * (upper - prev_bound)
            prev_bound = bound
        return self._max_ms

    def to_dict(self) -> dict[str, Any]:
        return {
            "count": self._count,
            "sum_ms": round(self._sum_ms, 2),
            "min_ms": round(self._min_ms, 2) if self._count else 0,
            "max_ms": round(self._max_ms, 2),
            "mean_ms": round(self.mean_ms, 2),
            "p50_ms": round(self.percentile(50), 2),
            "p95_ms": round(self.percentile(95), 2),
            "p99_ms": round(self.percentile(99), 2),
            "buckets": {
                str(b) if not math.isinf(b) else "+Inf": self._buckets[i]
                for i, b in enumerate(_LATENCY_BUCKETS_MS)
            },
        }

    def reset(self) -> None:
        self._buckets = [0] * len(_LATENCY_BUCKETS_MS)
        self._count = 0
        self._sum_ms = 0.0
        self._min_ms = float("inf")
        self._max_ms = 0.0


# ---------------------------------------------------------------------------
# MetricsCollector
# ---------------------------------------------------------------------------

class MetricsCollector:
    """Process-global metrics registry for Lucy.

    Attributes tracked
    ------------------
    Counters:
        tool_calls_total                 Every tool call attempted
        tool_errors_total[error_type]    Errors by classify_tool_error category
        tool_loops_total                 Loop-detection breaks
        unknown_tool_calls_total         LLM named a tool that didn't exist
        no_text_fallbacks_total          Tasks where LLM produced no text, used fallback
        tasks_total[status]              Tasks completed/failed
        calendar_fallbacks_total         Deterministic calendar fallback activations

    Histograms (milliseconds):
        tool_latency_ms                  Individual tool execution time
        llm_turn_latency_ms              One LLM round-trip (per turn)
        task_latency_ms                  Full task wall-clock time
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

        # Counters
        self._counters: dict[str, int] = defaultdict(int)
        self._labeled_counters: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

        # Histograms
        self._histograms: dict[str, Histogram] = {
            "tool_latency_ms": Histogram("tool_latency_ms"),
            "llm_turn_latency_ms": Histogram("llm_turn_latency_ms"),
            "task_latency_ms": Histogram("task_latency_ms"),
            "tool_retrieval_latency_ms": Histogram("tool_retrieval_latency_ms"),
        }

        # Process start time for uptime calculation
        self._started_at: float = time.monotonic()

    # ------------------------------------------------------------------
    # Async increment / record (safe for concurrent coroutines)
    # ------------------------------------------------------------------

    async def inc(self, name: str, value: int = 1) -> None:
        """Increment a plain counter."""
        async with self._lock:
            self._counters[name] += value

    async def inc_labeled(self, name: str, label: str, value: int = 1) -> None:
        """Increment a labeled counter (e.g. tool_errors_total[retryable])."""
        async with self._lock:
            self._labeled_counters[name][label] += value

    async def record(self, histogram: str, value_ms: float) -> None:
        """Record a latency value in milliseconds."""
        async with self._lock:
            if histogram in self._histograms:
                self._histograms[histogram].record(value_ms)

    # ------------------------------------------------------------------
    # Sync variants (for test code that does not run an event loop)
    # ------------------------------------------------------------------

    def _sync_inc(self, name: str, value: int = 1) -> None:
        self._counters[name] += value

    def _sync_inc_labeled(self, name: str, label: str, value: int = 1) -> None:
        self._labeled_counters[name][label] += value

    def _sync_record(self, histogram: str, value_ms: float) -> None:
        if histogram in self._histograms:
            self._histograms[histogram].record(value_ms)

    # ------------------------------------------------------------------
    # Context-manager timers
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def timer(self, histogram: str) -> AsyncIterator[None]:
        """Async context manager that auto-records elapsed ms."""
        t0 = time.monotonic()
        try:
            yield
        finally:
            elapsed_ms = (time.monotonic() - t0) * 1000
            await self.record(histogram, elapsed_ms)

    @contextmanager
    def sync_timer(self, histogram: str) -> Iterator[None]:
        """Sync context manager that auto-records elapsed ms (tests / sync code)."""
        t0 = time.monotonic()
        try:
            yield
        finally:
            elapsed_ms = (time.monotonic() - t0) * 1000
            self._sync_record(histogram, elapsed_ms)

    # ------------------------------------------------------------------
    # Named semantic helpers used by agent.py
    # ------------------------------------------------------------------

    async def tool_called(self, tool_name: str) -> None:
        await self.inc("tool_calls_total")

    async def tool_error(self, error_type: str) -> None:
        """Record a classified tool error (retryable/auth/invalid_params/fatal)."""
        await self.inc("tool_errors_total")
        await self.inc_labeled("tool_errors_by_type", error_type)

    async def tool_loop_detected(self) -> None:
        await self.inc("tool_loops_total")

    async def unknown_tool_called(self, tool_name: str) -> None:
        await self.inc("unknown_tool_calls_total")
        await self.inc_labeled("unknown_tool_names", tool_name)

    async def no_text_fallback(self) -> None:
        await self.inc("no_text_fallbacks_total")

    async def calendar_fallback(self) -> None:
        await self.inc("calendar_fallbacks_total")

    async def task_completed(self, status: str, elapsed_ms: float) -> None:
        await self.inc_labeled("tasks_total", status)
        await self.record("task_latency_ms", elapsed_ms)

    # ------------------------------------------------------------------
    # Snapshot / export
    # ------------------------------------------------------------------

    async def snapshot(self) -> dict[str, Any]:
        """Return a complete metrics snapshot (safe, lock-protected copy)."""
        async with self._lock:
            return self._build_snapshot()

    def snapshot_sync(self) -> dict[str, Any]:
        """Sync snapshot for tests / health endpoint sync callers."""
        return self._build_snapshot()

    def _build_snapshot(self) -> dict[str, Any]:
        uptime_s = round(time.monotonic() - self._started_at, 1)
        return {
            "uptime_seconds": uptime_s,
            "counters": dict(self._counters),
            "labeled_counters": {k: dict(v) for k, v in self._labeled_counters.items()},
            "histograms": {k: v.to_dict() for k, v in self._histograms.items()},
        }

    def reset_all(self) -> None:
        """Reset all metrics — intended for tests only."""
        self._counters.clear()
        for v in self._labeled_counters.values():
            v.clear()
        for h in self._histograms.values():
            h.reset()

    # ------------------------------------------------------------------
    # Structured log emission (called at task end for log-based monitoring)
    # ------------------------------------------------------------------

    async def emit_task_log(
        self,
        logger: Any,
        task_id: str,
        elapsed_ms: float,
        intent: str,
        model: str,
        tool_calls: int,
        tool_errors: int,
        tool_loops: int,
        unknown_tools: int,
        no_text: bool,
        status: str,
    ) -> None:
        """Emit a single structured log line summarising a task's reliability metrics."""
        snap = await self.snapshot()
        logger.info(
            "task_metrics",
            task_id=task_id,
            elapsed_ms=elapsed_ms,
            intent=intent,
            model=model,
            # Per-task counters
            task_tool_calls=tool_calls,
            task_tool_errors=tool_errors,
            task_tool_loops=tool_loops,
            task_unknown_tools=unknown_tools,
            task_no_text=no_text,
            task_status=status,
            # Global p95 latencies from histogram
            global_task_p95_ms=snap["histograms"]["task_latency_ms"]["p95_ms"],
            global_tool_p95_ms=snap["histograms"]["tool_latency_ms"]["p95_ms"],
            global_llm_turn_p95_ms=snap["histograms"]["llm_turn_latency_ms"]["p95_ms"],
            # Global reliability rates
            global_tool_loops=snap["counters"].get("tool_loops_total", 0),
            global_no_text=snap["counters"].get("no_text_fallbacks_total", 0),
            global_unknown_tools=snap["counters"].get("unknown_tool_calls_total", 0),
            global_tasks=snap["labeled_counters"].get("tasks_total", {}),
        )


# ---------------------------------------------------------------------------
# Process-global singleton
# ---------------------------------------------------------------------------

_collector: MetricsCollector | None = None


def get_metrics() -> MetricsCollector:
    """Return (or lazily create) the process-global MetricsCollector."""
    global _collector
    if _collector is None:
        _collector = MetricsCollector()
    return _collector
