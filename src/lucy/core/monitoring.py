"""Production monitoring, health checks, and alerting.

Provides:
1. Health endpoint data — component status for /health
2. Metrics aggregation — request counts, latencies, error rates
3. Alert thresholds — configurable warning/critical levels
4. Self-diagnosis — identify degraded components

This module collects data from existing subsystems (trace, rate_limiter,
request_queue, task_manager) and aggregates it into actionable health signals.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger()


# ═══════════════════════════════════════════════════════════════════════════
# HEALTH STATUS
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ComponentHealth:
    """Health status of a single component."""
    name: str
    status: str  # "healthy", "degraded", "unhealthy"
    latency_ms: float = 0.0
    message: str = ""
    last_check: float = 0.0


@dataclass
class SystemHealth:
    """Aggregate health of the entire system."""
    status: str  # "healthy", "degraded", "unhealthy"
    components: list[ComponentHealth] = field(default_factory=list)
    uptime_seconds: float = 0.0
    request_count: int = 0
    error_rate: float = 0.0  # 0.0 - 1.0
    p50_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    active_tasks: int = 0
    queue_depth: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "uptime_seconds": round(self.uptime_seconds, 1),
            "request_count": self.request_count,
            "error_rate": round(self.error_rate, 4),
            "latency_ms": {
                "p50": round(self.p50_latency_ms, 1),
                "p95": round(self.p95_latency_ms, 1),
                "p99": round(self.p99_latency_ms, 1),
            },
            "active_tasks": self.active_tasks,
            "queue_depth": self.queue_depth,
            "components": [
                {
                    "name": c.name,
                    "status": c.status,
                    "latency_ms": round(c.latency_ms, 1),
                    "message": c.message,
                }
                for c in self.components
            ],
        }


# ═══════════════════════════════════════════════════════════════════════════
# METRICS COLLECTOR
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class RequestMetric:
    """Single request metric."""
    timestamp: float
    latency_ms: float
    success: bool
    model: str = ""
    tool_count: int = 0


class MetricsCollector:
    """Collects and aggregates request-level metrics.

    Uses a sliding window (default 1 hour) to compute percentiles
    and error rates. Thread-safe via simple list append.
    """

    def __init__(self, window_seconds: float = 3600.0) -> None:
        self._window = window_seconds
        self._metrics: deque[RequestMetric] = deque(maxlen=10_000)
        self._start_time = time.monotonic()
        self._total_requests = 0
        self._total_errors = 0

    def record(self, metric: RequestMetric) -> None:
        """Record a request metric."""
        self._metrics.append(metric)
        self._total_requests += 1
        if not metric.success:
            self._total_errors += 1

    def _prune(self) -> list[RequestMetric]:
        """Get metrics within the window."""
        cutoff = time.monotonic() - self._window
        return [m for m in self._metrics if m.timestamp > cutoff]

    @property
    def uptime_seconds(self) -> float:
        return time.monotonic() - self._start_time

    def get_latency_percentiles(self) -> dict[str, float]:
        """Compute p50, p95, p99 latencies from recent window."""
        recent = self._prune()
        if not recent:
            return {"p50": 0.0, "p95": 0.0, "p99": 0.0}

        latencies = sorted(m.latency_ms for m in recent)
        n = len(latencies)

        return {
            "p50": latencies[int(n * 0.50)] if n > 0 else 0.0,
            "p95": latencies[int(n * 0.95)] if n > 1 else latencies[-1],
            "p99": latencies[int(n * 0.99)] if n > 2 else latencies[-1],
        }

    def get_error_rate(self) -> float:
        """Error rate in the recent window."""
        recent = self._prune()
        if not recent:
            return 0.0
        errors = sum(1 for m in recent if not m.success)
        return errors / len(recent)

    def get_request_rate(self) -> float:
        """Requests per minute in the recent window."""
        recent = self._prune()
        if not recent:
            return 0.0
        window_span = recent[-1].timestamp - recent[0].timestamp
        if window_span <= 0:
            return 0.0
        return len(recent) / (window_span / 60.0)

    def get_model_breakdown(self) -> dict[str, int]:
        """Request count by model in recent window."""
        recent = self._prune()
        breakdown: dict[str, int] = {}
        for m in recent:
            model = m.model or "unknown"
            breakdown[model] = breakdown.get(model, 0) + 1
        return breakdown

    @property
    def total_requests(self) -> int:
        return self._total_requests

    @property
    def total_errors(self) -> int:
        return self._total_errors


# ═══════════════════════════════════════════════════════════════════════════
# ALERTING
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class AlertThresholds:
    """Configurable thresholds for health degradation."""
    # Error rate
    error_rate_warning: float = 0.05   # 5%
    error_rate_critical: float = 0.15  # 15%

    # Latency (ms)
    p95_latency_warning: float = 15_000.0   # 15s
    p95_latency_critical: float = 45_000.0  # 45s

    # Queue
    queue_depth_warning: int = 30
    queue_depth_critical: int = 80

    # Tasks
    active_tasks_warning: int = 15
    active_tasks_critical: int = 40


class AlertManager:
    """Evaluates health against thresholds and emits alerts."""

    def __init__(
        self,
        thresholds: AlertThresholds | None = None,
        slack_alert_channel: str = "",
    ) -> None:
        self.thresholds = thresholds or AlertThresholds()
        self._slack_channel = slack_alert_channel
        self._last_alert: dict[str, float] = {}  # alert_key → timestamp
        self._alert_cooldown = 300.0  # 5 min between duplicate alerts

    def evaluate(self, health: SystemHealth) -> list[dict[str, Any]]:
        """Evaluate health and return any triggered alerts."""
        alerts: list[dict[str, Any]] = []
        t = self.thresholds

        # Error rate
        if health.error_rate >= t.error_rate_critical:
            alerts.append({
                "level": "critical",
                "component": "error_rate",
                "message": f"Error rate at {health.error_rate:.1%} (threshold: {t.error_rate_critical:.0%})",
            })
        elif health.error_rate >= t.error_rate_warning:
            alerts.append({
                "level": "warning",
                "component": "error_rate",
                "message": f"Error rate elevated at {health.error_rate:.1%}",
            })

        # Latency
        if health.p95_latency_ms >= t.p95_latency_critical:
            alerts.append({
                "level": "critical",
                "component": "latency",
                "message": f"P95 latency at {health.p95_latency_ms:.0f}ms (threshold: {t.p95_latency_critical:.0f}ms)",
            })
        elif health.p95_latency_ms >= t.p95_latency_warning:
            alerts.append({
                "level": "warning",
                "component": "latency",
                "message": f"P95 latency elevated at {health.p95_latency_ms:.0f}ms",
            })

        # Queue depth
        if health.queue_depth >= t.queue_depth_critical:
            alerts.append({
                "level": "critical",
                "component": "queue",
                "message": f"Queue depth at {health.queue_depth} (threshold: {t.queue_depth_critical})",
            })
        elif health.queue_depth >= t.queue_depth_warning:
            alerts.append({
                "level": "warning",
                "component": "queue",
                "message": f"Queue depth elevated at {health.queue_depth}",
            })

        # Filter by cooldown
        now = time.monotonic()
        filtered: list[dict[str, Any]] = []
        for alert in alerts:
            key = f"{alert['component']}_{alert['level']}"
            last = self._last_alert.get(key, 0.0)
            if now - last >= self._alert_cooldown:
                self._last_alert[key] = now
                filtered.append(alert)

        return filtered


# ═══════════════════════════════════════════════════════════════════════════
# HEALTH CHECK RUNNER
# ═══════════════════════════════════════════════════════════════════════════

async def check_component_health(name: str, check_fn: Any) -> ComponentHealth:
    """Run a health check function with timeout."""
    t0 = time.monotonic()
    try:
        result = await asyncio.wait_for(check_fn(), timeout=5.0)
        latency = (time.monotonic() - t0) * 1000

        if isinstance(result, bool):
            status = "healthy" if result else "unhealthy"
            message = ""
        elif isinstance(result, dict):
            status = result.get("status", "healthy")
            message = result.get("message", "")
        else:
            status = "healthy"
            message = str(result)

        return ComponentHealth(
            name=name,
            status=status,
            latency_ms=latency,
            message=message,
            last_check=time.monotonic(),
        )

    except asyncio.TimeoutError:
        return ComponentHealth(
            name=name,
            status="unhealthy",
            latency_ms=5000.0,
            message="Health check timed out (5s)",
            last_check=time.monotonic(),
        )
    except Exception as e:
        latency = (time.monotonic() - t0) * 1000
        return ComponentHealth(
            name=name,
            status="unhealthy",
            latency_ms=latency,
            message=str(e)[:200],
            last_check=time.monotonic(),
        )


async def get_system_health(metrics: MetricsCollector) -> SystemHealth:
    """Run all health checks and return aggregate status."""

    # Component checks
    checks: list[tuple[str, Any]] = []

    # 1. Database
    async def check_db():
        from lucy.db.session import async_session
        async with async_session() as session:
            await session.execute("SELECT 1")
        return True
    checks.append(("database", check_db))

    # 2. OpenRouter (LLM)
    async def check_llm():
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get("https://openrouter.ai/api/v1/models", headers={"accept": "application/json"})
            return resp.status_code == 200
    checks.append(("openrouter_llm", check_llm))

    # 3. Composio
    async def check_composio():
        try:
            from lucy.integrations.composio_client import get_composio_client
            client = get_composio_client()
            return client._composio is not None
        except Exception:
            return False
    checks.append(("composio", check_composio))

    # 4. CamoFox browser
    async def check_browser():
        try:
            from lucy.integrations.camofox import get_camofox_client
            return await get_camofox_client().is_healthy()
        except Exception:
            return False
    checks.append(("camofox_browser", check_browser))

    # Run all checks in parallel
    component_tasks = [
        check_component_health(name, fn) for name, fn in checks
    ]
    components = await asyncio.gather(*component_tasks)

    # Aggregate metrics
    percentiles = metrics.get_latency_percentiles()
    error_rate = metrics.get_error_rate()

    # Queue depth
    try:
        from lucy.core.request_queue import get_request_queue
        queue_depth = get_request_queue().metrics.get("queue_size", 0)
    except Exception:
        queue_depth = 0

    # Active tasks
    try:
        from lucy.core.task_manager import get_task_manager, TaskState
        tm = get_task_manager()
        active_tasks = sum(
            1 for tasks in tm._workspace_tasks.values()
            for t in tasks
            if t.state in (TaskState.PENDING, TaskState.ACKNOWLEDGED, TaskState.WORKING)
        )
    except Exception:
        active_tasks = 0

    # Determine overall status
    unhealthy_count = sum(1 for c in components if c.status == "unhealthy")
    degraded_count = sum(1 for c in components if c.status == "degraded")

    if unhealthy_count >= 2 or error_rate > 0.15:
        overall = "unhealthy"
    elif unhealthy_count >= 1 or degraded_count >= 2 or error_rate > 0.05:
        overall = "degraded"
    else:
        overall = "healthy"

    return SystemHealth(
        status=overall,
        components=list(components),
        uptime_seconds=metrics.uptime_seconds,
        request_count=metrics.total_requests,
        error_rate=error_rate,
        p50_latency_ms=percentiles["p50"],
        p95_latency_ms=percentiles["p95"],
        p99_latency_ms=percentiles["p99"],
        active_tasks=active_tasks,
        queue_depth=queue_depth,
    )


# ═══════════════════════════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════════════════════════

_metrics: MetricsCollector | None = None
_alert_manager: AlertManager | None = None


def get_metrics() -> MetricsCollector:
    global _metrics
    if _metrics is None:
        _metrics = MetricsCollector()
    return _metrics


def get_alert_manager() -> AlertManager:
    global _alert_manager
    if _alert_manager is None:
        _alert_manager = AlertManager()
    return _alert_manager
