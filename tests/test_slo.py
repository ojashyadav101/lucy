"""Tests for the SLO evaluator."""

from __future__ import annotations

import pytest

from lucy.observability.slo import (
    SLO_TARGETS,
    SLOEvaluator,
    SLOReport,
    SLOTarget,
    get_slo_evaluator,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _snap(
    tool_calls: int = 0,
    tool_errors: int = 0,
    unknown_tools: int = 0,
    no_text_fallbacks: int = 0,
    tasks_completed: int = 0,
    tasks_failed: int = 0,
    tool_p95_ms: float = 0.0,
    task_p95_ms: float = 0.0,
    tool_hist_count: int = 0,
    task_hist_count: int = 0,
    uptime: float = 100.0,
) -> dict:
    return {
        "uptime_seconds": uptime,
        "counters": {
            "tool_calls_total": tool_calls,
            "tool_errors_total": tool_errors,
            "unknown_tool_calls_total": unknown_tools,
            "no_text_fallbacks_total": no_text_fallbacks,
        },
        "labeled_counters": {
            "tasks_total": {
                "completed": tasks_completed,
                "failed": tasks_failed,
            }
        },
        "histograms": {
            "tool_latency_ms": {
                "count": tool_hist_count,
                "p50_ms": tool_p95_ms * 0.7,
                "p95_ms": tool_p95_ms,
                "p99_ms": tool_p95_ms * 1.1,
            },
            "task_latency_ms": {
                "count": task_hist_count,
                "p50_ms": task_p95_ms * 0.7,
                "p95_ms": task_p95_ms,
                "p99_ms": task_p95_ms * 1.1,
            },
        },
    }


evaluator = SLOEvaluator()


# ─────────────────────────────────────────────────────────────────────────────
# SLO targets definition
# ─────────────────────────────────────────────────────────────────────────────

class TestSLOTargetDefinitions:
    def test_six_slos_defined(self):
        assert len(SLO_TARGETS) == 6

    def test_all_have_names(self):
        names = {s.name for s in SLO_TARGETS}
        assert "tool_success_rate" in names
        assert "no_text_fallback_rate" in names
        assert "unknown_tool_rate" in names
        assert "tool_p95_latency_ms" in names
        assert "task_p95_latency_ms" in names

    def test_directions_valid(self):
        for slo in SLO_TARGETS:
            assert slo.direction in ("min", "max")


# ─────────────────────────────────────────────────────────────────────────────
# Insufficient data → all passing
# ─────────────────────────────────────────────────────────────────────────────

class TestInsufficientData:
    def test_empty_snapshot_all_pass(self):
        report = evaluator.evaluate_snapshot(_snap())
        assert report.all_passing

    def test_empty_snapshot_messages_insufficient(self):
        report = evaluator.evaluate_snapshot(_snap())
        for result in report.results:
            assert result.measured is None or "Insufficient" in result.message

    def test_below_minimum_calls_passes(self):
        # Only 5 tool calls — below min=10
        report = evaluator.evaluate_snapshot(_snap(tool_calls=5, tool_errors=5))
        assert report.all_passing


# ─────────────────────────────────────────────────────────────────────────────
# Tool success rate SLO
# ─────────────────────────────────────────────────────────────────────────────

class TestToolSuccessRateSLO:
    def test_passes_at_100_percent(self):
        report = evaluator.evaluate_snapshot(_snap(tool_calls=100, tool_errors=0))
        sr = next(r for r in report.results if r.slo.name == "tool_success_rate")
        assert sr.passing

    def test_passes_at_99_percent(self):
        report = evaluator.evaluate_snapshot(_snap(tool_calls=100, tool_errors=1))
        sr = next(r for r in report.results if r.slo.name == "tool_success_rate")
        assert sr.passing

    def test_fails_at_95_percent(self):
        report = evaluator.evaluate_snapshot(_snap(tool_calls=100, tool_errors=5))
        sr = next(r for r in report.results if r.slo.name == "tool_success_rate")
        assert not sr.passing

    def test_measured_value_correct(self):
        report = evaluator.evaluate_snapshot(_snap(tool_calls=200, tool_errors=4))
        sr = next(r for r in report.results if r.slo.name == "tool_success_rate")
        assert sr.measured == pytest.approx(98.0)


# ─────────────────────────────────────────────────────────────────────────────
# No-text fallback rate SLO
# ─────────────────────────────────────────────────────────────────────────────

class TestNoTextFallbackSLO:
    def test_passes_at_zero(self):
        report = evaluator.evaluate_snapshot(_snap(tasks_completed=100, no_text_fallbacks=0))
        nt = next(r for r in report.results if r.slo.name == "no_text_fallback_rate")
        assert nt.passing

    def test_fails_when_rate_too_high(self):
        # 3 out of 100 = 3% > 0.5% threshold
        report = evaluator.evaluate_snapshot(_snap(tasks_completed=100, no_text_fallbacks=3))
        nt = next(r for r in report.results if r.slo.name == "no_text_fallback_rate")
        assert not nt.passing

    def test_passes_at_exactly_0_5_percent(self):
        # Exact: 1 / 200 = 0.5%
        report = evaluator.evaluate_snapshot(_snap(tasks_completed=200, no_text_fallbacks=1))
        nt = next(r for r in report.results if r.slo.name == "no_text_fallback_rate")
        assert nt.passing


# ─────────────────────────────────────────────────────────────────────────────
# Unknown tool rate SLO
# ─────────────────────────────────────────────────────────────────────────────

class TestUnknownToolRateSLO:
    def test_passes_at_zero(self):
        report = evaluator.evaluate_snapshot(_snap(tool_calls=50, unknown_tools=0))
        ut = next(r for r in report.results if r.slo.name == "unknown_tool_rate")
        assert ut.passing

    def test_fails_above_0_1_percent(self):
        # 2 / 100 = 2% > 0.1%
        report = evaluator.evaluate_snapshot(_snap(tool_calls=100, unknown_tools=2))
        ut = next(r for r in report.results if r.slo.name == "unknown_tool_rate")
        assert not ut.passing


# ─────────────────────────────────────────────────────────────────────────────
# Latency SLOs
# ─────────────────────────────────────────────────────────────────────────────

class TestLatencySLOs:
    def test_tool_p95_passes_below_8000ms(self):
        report = evaluator.evaluate_snapshot(
            _snap(tool_p95_ms=4000.0, tool_hist_count=10)
        )
        tp = next(r for r in report.results if r.slo.name == "tool_p95_latency_ms")
        assert tp.passing

    def test_tool_p95_fails_above_8000ms(self):
        report = evaluator.evaluate_snapshot(
            _snap(tool_p95_ms=9000.0, tool_hist_count=10)
        )
        tp = next(r for r in report.results if r.slo.name == "tool_p95_latency_ms")
        assert not tp.passing

    def test_task_p95_passes_below_30000ms(self):
        report = evaluator.evaluate_snapshot(
            _snap(task_p95_ms=15000.0, task_hist_count=10)
        )
        tkp = next(r for r in report.results if r.slo.name == "task_p95_latency_ms")
        assert tkp.passing

    def test_task_p95_fails_above_30000ms(self):
        report = evaluator.evaluate_snapshot(
            _snap(task_p95_ms=35000.0, task_hist_count=10)
        )
        tkp = next(r for r in report.results if r.slo.name == "task_p95_latency_ms")
        assert not tkp.passing

    def test_insufficient_histogram_data_passes(self):
        # Only 3 samples — below minimum of 5
        report = evaluator.evaluate_snapshot(
            _snap(tool_p95_ms=99999.0, tool_hist_count=3)
        )
        tp = next(r for r in report.results if r.slo.name == "tool_p95_latency_ms")
        assert tp.passing  # Not enough data — passes by default


# ─────────────────────────────────────────────────────────────────────────────
# SLOReport structure
# ─────────────────────────────────────────────────────────────────────────────

class TestSLOReport:
    def test_all_passing_when_no_breaches(self):
        report = evaluator.evaluate_snapshot(_snap())
        assert report.all_passing

    def test_failing_list_populated(self):
        # High error rate
        report = evaluator.evaluate_snapshot(_snap(tool_calls=100, tool_errors=20))
        assert len(report.failing) >= 1
        assert all(not r.passing for r in report.failing)

    def test_to_dict_structure(self):
        report = evaluator.evaluate_snapshot(_snap())
        d = report.to_dict()
        assert "overall" in d
        assert "slos" in d
        assert "total_tasks" in d
        assert "total_tool_calls" in d
        assert "uptime_seconds" in d

    def test_to_dict_slo_items_have_status(self):
        report = evaluator.evaluate_snapshot(_snap())
        for item in report.to_dict()["slos"]:
            assert item["status"] in ("PASS", "FAIL")

    def test_overall_fail_when_breach(self):
        report = evaluator.evaluate_snapshot(_snap(tool_calls=100, tool_errors=20))
        assert report.to_dict()["overall"] == "FAIL"

    def test_overall_pass_on_empty_data(self):
        report = evaluator.evaluate_snapshot(_snap())
        assert report.to_dict()["overall"] == "PASS"


# ─────────────────────────────────────────────────────────────────────────────
# SLO evaluator — check_and_alert
# ─────────────────────────────────────────────────────────────────────────────

class TestSLOAlert:
    @pytest.mark.asyncio
    async def test_check_and_alert_returns_report(self):
        # The async evaluate() reads from real MetricsCollector
        # We only test that check_and_alert doesn't raise
        evaluator = SLOEvaluator()
        report = await evaluator.check_and_alert()
        assert isinstance(report, SLOReport)

    @pytest.mark.asyncio
    async def test_check_and_alert_accepts_custom_logger(self):
        import structlog
        log = structlog.get_logger()
        evaluator = SLOEvaluator()
        report = await evaluator.check_and_alert(alert_logger=log)
        assert isinstance(report, SLOReport)


# ─────────────────────────────────────────────────────────────────────────────
# Singleton
# ─────────────────────────────────────────────────────────────────────────────

class TestSLOEvaluatorSingleton:
    def test_get_slo_evaluator_same_instance(self):
        e1 = get_slo_evaluator()
        e2 = get_slo_evaluator()
        assert e1 is e2
