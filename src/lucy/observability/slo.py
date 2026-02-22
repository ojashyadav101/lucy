"""SLO Evaluator — checks live metrics against Lucy's production SLO targets.

SLO Targets (from production-hardening-roadmap.md)
---------------------------------------------------
  tool_success_rate           >= 99.0%
  no_text_fallback_rate       <=  0.5%
  unknown_tool_rate           <=  0.1%
  tool_p95_latency_ms         <= 8 000 ms
  tool_retrieval_p95_ms       <=   500 ms   (tool_latency_ms p95)
  task_p95_latency_ms         <= 30 000 ms  (generous end-to-end budget)

Usage
-----
    evaluator = get_slo_evaluator()
    report = await evaluator.evaluate()
    await evaluator.check_and_alert(logger)

    # Endpoint snapshot
    return report.to_dict()

Alert behaviour
---------------
When any SLO is breached the evaluator emits a structured ``slo_breach``
log line (ERROR level) that monitoring systems (e.g. Datadog, Grafana Loki)
can alert on.  It does NOT raise exceptions — it is purely observational.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger()


# ─────────────────────────────────────────────────────────────────────────────
# SLO definitions
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SLOTarget:
    """One SLO definition."""
    name: str
    description: str
    threshold: float
    direction: str   # "min" (value must be >= threshold) or "max" (value must be <= threshold)
    unit: str = ""


SLO_TARGETS: list[SLOTarget] = [
    SLOTarget(
        name="tool_success_rate",
        description="Fraction of tool calls that succeeded",
        threshold=99.0,
        direction="min",
        unit="%",
    ),
    SLOTarget(
        name="no_text_fallback_rate",
        description="Fraction of tasks that fell back to synthesised response",
        threshold=0.5,
        direction="max",
        unit="%",
    ),
    SLOTarget(
        name="unknown_tool_rate",
        description="Fraction of tool calls targeting an unregistered tool",
        threshold=0.1,
        direction="max",
        unit="%",
    ),
    SLOTarget(
        name="tool_p95_latency_ms",
        description="p95 tool execution latency",
        threshold=8_000.0,
        direction="max",
        unit="ms",
    ),
    SLOTarget(
        name="tool_retrieval_p95_ms",
        description="p95 per-batch tool retrieval latency",
        threshold=500.0,
        direction="max",
        unit="ms",
    ),
    SLOTarget(
        name="task_p95_latency_ms",
        description="p95 end-to-end task latency",
        threshold=30_000.0,
        direction="max",
        unit="ms",
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
# Evaluation result types
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SLOResult:
    """Evaluation result for one SLO."""
    slo: SLOTarget
    measured: float | None    # None means insufficient data
    passing: bool
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.slo.name,
            "description": self.slo.description,
            "threshold": f"{self.slo.threshold}{self.slo.unit}",
            "direction": self.slo.direction,
            "measured": (
                f"{round(self.measured, 2)}{self.slo.unit}"
                if self.measured is not None else "insufficient_data"
            ),
            "status": "PASS" if self.passing else "FAIL",
            "message": self.message,
        }


@dataclass
class SLOReport:
    """Full SLO evaluation report."""
    results: list[SLOResult] = field(default_factory=list)
    total_tasks: int = 0
    total_tool_calls: int = 0
    uptime_seconds: float = 0.0

    @property
    def all_passing(self) -> bool:
        return all(r.passing for r in self.results)

    @property
    def failing(self) -> list[SLOResult]:
        return [r for r in self.results if not r.passing]

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall": "PASS" if self.all_passing else "FAIL",
            "total_tasks": self.total_tasks,
            "total_tool_calls": self.total_tool_calls,
            "uptime_seconds": self.uptime_seconds,
            "slos": [r.to_dict() for r in self.results],
        }


# ─────────────────────────────────────────────────────────────────────────────
# Evaluator
# ─────────────────────────────────────────────────────────────────────────────

class SLOEvaluator:
    """Evaluates live MetricsCollector data against defined SLO targets."""

    async def evaluate(self) -> SLOReport:
        """Compute the current SLO status from live metrics."""
        from lucy.observability.metrics import get_metrics
        snap = await get_metrics().snapshot()
        return self._evaluate_snapshot(snap)

    def evaluate_snapshot(self, snap: dict[str, Any]) -> SLOReport:
        """Evaluate a pre-fetched metrics snapshot (useful in tests)."""
        return self._evaluate_snapshot(snap)

    def _evaluate_snapshot(self, snap: dict[str, Any]) -> SLOReport:
        counters = snap.get("counters", {})
        labeled = snap.get("labeled_counters", {})
        histograms = snap.get("histograms", {})
        uptime = snap.get("uptime_seconds", 0.0)

        # Derived totals
        tool_calls_total = counters.get("tool_calls_total", 0)
        tool_errors_total = counters.get("tool_errors_total", 0)
        tool_loops_total = counters.get("tool_loops_total", 0)
        unknown_tool_calls = counters.get("unknown_tool_calls_total", 0)
        no_text_fallbacks = counters.get("no_text_fallbacks_total", 0)

        tasks_by_status = labeled.get("tasks_total", {})
        tasks_completed = tasks_by_status.get("completed", 0)
        tasks_failed = tasks_by_status.get("failed", 0)
        total_tasks = tasks_completed + tasks_failed

        # ── Compute measured values ──────────────────────────────────────────
        measured: dict[str, float | None] = {}

        # 1. Tool success rate
        if tool_calls_total >= 10:
            measured["tool_success_rate"] = (
                (tool_calls_total - tool_errors_total) / tool_calls_total * 100
            )
        else:
            measured["tool_success_rate"] = None  # not enough data

        # 2. No-text fallback rate (as % of completed tasks)
        if tasks_completed >= 5:
            measured["no_text_fallback_rate"] = no_text_fallbacks / tasks_completed * 100
        else:
            measured["no_text_fallback_rate"] = None

        # 3. Unknown tool rate (as % of tool calls)
        if tool_calls_total >= 10:
            measured["unknown_tool_rate"] = unknown_tool_calls / tool_calls_total * 100
        else:
            measured["unknown_tool_rate"] = None

        # 4. Tool p95 latency (from tool_latency_ms histogram)
        tool_hist = histograms.get("tool_latency_ms", {})
        if tool_hist.get("count", 0) >= 5:
            measured["tool_p95_latency_ms"] = tool_hist.get("p95_ms", 0.0)
        else:
            measured["tool_p95_latency_ms"] = None

        # 5. Tool retrieval p95 — dedicated histogram for BM25 index retrieval
        #    (sub-50ms in practice; threshold is 500ms). If fewer than 5 samples
        #    the SLO passes by default (insufficient data).
        retrieval_hist = histograms.get("tool_retrieval_latency_ms", {})
        if retrieval_hist.get("count", 0) >= 5:
            measured["tool_retrieval_p95_ms"] = retrieval_hist.get("p95_ms", 0.0)
        else:
            measured["tool_retrieval_p95_ms"] = None

        # 6. Task p95 latency
        task_hist = histograms.get("task_latency_ms", {})
        if task_hist.get("count", 0) >= 5:
            measured["task_p95_latency_ms"] = task_hist.get("p95_ms", 0.0)
        else:
            measured["task_p95_latency_ms"] = None

        # ── Evaluate each SLO ────────────────────────────────────────────────
        results: list[SLOResult] = []
        for slo in SLO_TARGETS:
            value = measured.get(slo.name)
            if value is None or math.isnan(value):
                results.append(SLOResult(
                    slo=slo,
                    measured=None,
                    passing=True,   # Not enough data — don't fail
                    message="Insufficient data (passing by default)",
                ))
                continue

            if slo.direction == "min":
                passing = value >= slo.threshold
            else:
                passing = value <= slo.threshold

            if passing:
                message = f"OK — {round(value, 2)}{slo.unit} (threshold {slo.threshold}{slo.unit})"
            else:
                message = (
                    f"BREACH — {round(value, 2)}{slo.unit} "
                    f"{'below' if slo.direction == 'min' else 'above'} "
                    f"threshold {slo.threshold}{slo.unit}"
                )

            results.append(SLOResult(slo=slo, measured=value, passing=passing, message=message))

        return SLOReport(
            results=results,
            total_tasks=total_tasks,
            total_tool_calls=tool_calls_total,
            uptime_seconds=uptime,
        )

    async def check_and_alert(self, alert_logger: Any | None = None) -> SLOReport:
        """Evaluate SLOs and emit structured alert logs for any breaches.

        Args:
            alert_logger: structlog logger instance. Defaults to module logger.

        Returns:
            The full SLOReport (breaches and passes).
        """
        log = alert_logger or logger
        report = await self.evaluate()

        for result in report.failing:
            log.error(
                "slo_breach",
                slo_name=result.slo.name,
                measured=result.measured,
                threshold=result.slo.threshold,
                direction=result.slo.direction,
                unit=result.slo.unit,
                message=result.message,
            )

        if report.all_passing:
            log.info("slo_all_passing", total_slos=len(report.results))

        return report


# ─────────────────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────────────────

_evaluator: SLOEvaluator | None = None


def get_slo_evaluator() -> SLOEvaluator:
    global _evaluator
    if _evaluator is None:
        _evaluator = SLOEvaluator()
    return _evaluator
