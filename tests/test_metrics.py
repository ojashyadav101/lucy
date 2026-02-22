"""Phase 1 metrics instrumentation tests.

These tests run entirely in-process (no DB, no Slack, no OpenClaw).
They verify:
  1. Counter increment semantics (plain + labeled)
  2. Latency histogram recording, bucket placement, and percentile estimates
  3. Semantic helper methods (tool_called, tool_error, tool_loop_detected, etc.)
  4. Snapshot structure and completeness
  5. Sync vs async paths produce the same results
  6. reset_all() properly zeros state
"""

from __future__ import annotations

import asyncio
import math
import time

import pytest

from lucy.observability.metrics import (
    Histogram,
    MetricsCollector,
    _LATENCY_BUCKETS_MS,
    get_metrics,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run(coro):
    """Run a coroutine in a fresh event loop (for sync test contexts)."""
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture()
def mc() -> MetricsCollector:
    """Fresh MetricsCollector for each test (avoids global state bleed)."""
    collector = MetricsCollector()
    return collector


# ---------------------------------------------------------------------------
# Histogram unit tests
# ---------------------------------------------------------------------------


class TestHistogram:
    def test_count_increases(self):
        h = Histogram("test")
        assert h.count == 0
        h.record(50)
        h.record(100)
        assert h.count == 2

    def test_bucket_placement(self):
        h = Histogram("test")
        # 5ms should land in the first bucket (≤5)
        h.record(5)
        assert h._buckets[0] == 1
        # 1000ms should land in the ≤1000 bucket
        h.record(1000)
        bucket_idx = list(_LATENCY_BUCKETS_MS).index(1000)
        assert h._buckets[bucket_idx] == 1

    def test_above_max_bucket_goes_to_inf(self):
        h = Histogram("test")
        h.record(1_000_000)  # 1000 seconds — far above all buckets
        inf_idx = _LATENCY_BUCKETS_MS.index(float("inf"))
        assert h._buckets[inf_idx] == 1

    def test_mean_calculation(self):
        h = Histogram("test")
        h.record(100)
        h.record(200)
        assert abs(h.mean_ms - 150.0) < 0.01

    def test_percentile_zero_count(self):
        h = Histogram("test")
        assert h.percentile(95) == 0.0

    def test_percentile_single_value(self):
        h = Histogram("test")
        h.record(250)
        # p50 and p95 should both resolve to ≤500 bucket upper bound
        assert h.percentile(50) <= 500
        assert h.percentile(95) <= 500

    def test_to_dict_keys(self):
        h = Histogram("test")
        h.record(300)
        d = h.to_dict()
        for key in ("count", "sum_ms", "min_ms", "max_ms", "mean_ms", "p50_ms", "p95_ms", "p99_ms", "buckets"):
            assert key in d, f"Missing key: {key}"

    def test_reset_zeroes_all(self):
        h = Histogram("test")
        h.record(100)
        h.record(200)
        h.reset()
        assert h.count == 0
        assert h._sum_ms == 0.0
        assert all(b == 0 for b in h._buckets)


# ---------------------------------------------------------------------------
# MetricsCollector — counter tests
# ---------------------------------------------------------------------------


class TestMetricsCollectorCounters:
    def test_inc_plain(self, mc):
        run(mc.inc("foo"))
        run(mc.inc("foo"))
        snap = mc.snapshot_sync()
        assert snap["counters"]["foo"] == 2

    def test_inc_labeled(self, mc):
        run(mc.inc_labeled("errors", "retryable"))
        run(mc.inc_labeled("errors", "retryable"))
        run(mc.inc_labeled("errors", "fatal"))
        snap = mc.snapshot_sync()
        assert snap["labeled_counters"]["errors"]["retryable"] == 2
        assert snap["labeled_counters"]["errors"]["fatal"] == 1

    def test_sync_inc(self, mc):
        mc._sync_inc("bar", 3)
        snap = mc.snapshot_sync()
        assert snap["counters"]["bar"] == 3

    def test_sync_inc_labeled(self, mc):
        mc._sync_inc_labeled("cat", "x", 5)
        snap = mc.snapshot_sync()
        assert snap["labeled_counters"]["cat"]["x"] == 5

    def test_multiple_counters_independent(self, mc):
        run(mc.inc("a"))
        run(mc.inc("b"))
        snap = mc.snapshot_sync()
        assert snap["counters"]["a"] == 1
        assert snap["counters"]["b"] == 1

    def test_reset_clears_counters(self, mc):
        run(mc.inc("x"))
        mc.reset_all()
        snap = mc.snapshot_sync()
        assert snap["counters"].get("x", 0) == 0


# ---------------------------------------------------------------------------
# MetricsCollector — histogram tests
# ---------------------------------------------------------------------------


class TestMetricsCollectorHistograms:
    def test_record_tool_latency(self, mc):
        run(mc.record("tool_latency_ms", 150))
        snap = mc.snapshot_sync()
        assert snap["histograms"]["tool_latency_ms"]["count"] == 1
        assert snap["histograms"]["tool_latency_ms"]["max_ms"] == 150.0

    def test_record_llm_turn_latency(self, mc):
        run(mc.record("llm_turn_latency_ms", 800))
        snap = mc.snapshot_sync()
        assert snap["histograms"]["llm_turn_latency_ms"]["count"] == 1

    def test_record_task_latency(self, mc):
        run(mc.record("task_latency_ms", 3500))
        snap = mc.snapshot_sync()
        assert snap["histograms"]["task_latency_ms"]["count"] == 1
        assert snap["histograms"]["task_latency_ms"]["max_ms"] == 3500.0

    def test_unknown_histogram_name_ignored(self, mc):
        # Should not raise
        run(mc.record("nonexistent_histogram", 100))

    def test_p95_across_many_samples(self, mc):
        """With 100 samples from 1–100ms, p95 should be around 95ms."""
        for i in range(1, 101):
            mc._sync_record("task_latency_ms", float(i))
        snap = mc.snapshot_sync()
        p95 = snap["histograms"]["task_latency_ms"]["p95_ms"]
        # Allow a wide tolerance for bucket interpolation
        assert 80 <= p95 <= 100, f"p95 out of expected range: {p95}"

    def test_sync_timer_records(self, mc):
        with mc.sync_timer("tool_latency_ms"):
            time.sleep(0.01)
        snap = mc.snapshot_sync()
        assert snap["histograms"]["tool_latency_ms"]["count"] == 1
        assert snap["histograms"]["tool_latency_ms"]["max_ms"] >= 10

    def test_async_timer_records(self, mc):
        async def _inner():
            async with mc.timer("tool_latency_ms"):
                await asyncio.sleep(0.01)

        run(_inner())
        snap = mc.snapshot_sync()
        assert snap["histograms"]["tool_latency_ms"]["count"] == 1
        assert snap["histograms"]["tool_latency_ms"]["max_ms"] >= 10


# ---------------------------------------------------------------------------
# MetricsCollector — semantic helper tests
# ---------------------------------------------------------------------------


class TestSemanticHelpers:
    def test_tool_called(self, mc):
        run(mc.tool_called("GOOGLECALENDAR_LIST_EVENTS"))
        snap = mc.snapshot_sync()
        assert snap["counters"]["tool_calls_total"] == 1

    def test_tool_error_increments_total_and_label(self, mc):
        run(mc.tool_error("retryable"))
        run(mc.tool_error("auth"))
        snap = mc.snapshot_sync()
        assert snap["counters"]["tool_errors_total"] == 2
        assert snap["labeled_counters"]["tool_errors_by_type"]["retryable"] == 1
        assert snap["labeled_counters"]["tool_errors_by_type"]["auth"] == 1

    def test_tool_loop_detected(self, mc):
        run(mc.tool_loop_detected())
        run(mc.tool_loop_detected())
        snap = mc.snapshot_sync()
        assert snap["counters"]["tool_loops_total"] == 2

    def test_unknown_tool_called(self, mc):
        run(mc.unknown_tool_called("GHOST_TOOL"))
        snap = mc.snapshot_sync()
        assert snap["counters"]["unknown_tool_calls_total"] == 1
        assert snap["labeled_counters"]["unknown_tool_names"]["GHOST_TOOL"] == 1

    def test_no_text_fallback(self, mc):
        run(mc.no_text_fallback())
        snap = mc.snapshot_sync()
        assert snap["counters"]["no_text_fallbacks_total"] == 1

    def test_calendar_fallback(self, mc):
        run(mc.calendar_fallback())
        snap = mc.snapshot_sync()
        assert snap["counters"]["calendar_fallbacks_total"] == 1

    def test_task_completed_increments_label_and_histogram(self, mc):
        run(mc.task_completed("completed", 2500.0))
        snap = mc.snapshot_sync()
        assert snap["labeled_counters"]["tasks_total"]["completed"] == 1
        assert snap["histograms"]["task_latency_ms"]["count"] == 1

    def test_task_completed_failed_status(self, mc):
        run(mc.task_completed("failed", 1000.0))
        snap = mc.snapshot_sync()
        assert snap["labeled_counters"]["tasks_total"]["failed"] == 1


# ---------------------------------------------------------------------------
# Snapshot structure tests
# ---------------------------------------------------------------------------


class TestSnapshot:
    def test_snapshot_top_level_keys(self, mc):
        snap = mc.snapshot_sync()
        assert "uptime_seconds" in snap
        assert "counters" in snap
        assert "labeled_counters" in snap
        assert "histograms" in snap

    def test_snapshot_histogram_keys_present(self, mc):
        snap = mc.snapshot_sync()
        for name in ("tool_latency_ms", "llm_turn_latency_ms", "task_latency_ms"):
            assert name in snap["histograms"], f"Missing histogram: {name}"

    def test_snapshot_async_matches_sync(self, mc):
        run(mc.inc("z"))
        async_snap = run(mc.snapshot())
        sync_snap = mc.snapshot_sync()
        assert async_snap["counters"]["z"] == sync_snap["counters"]["z"]

    def test_uptime_increases(self, mc):
        snap1 = mc.snapshot_sync()
        time.sleep(0.05)
        snap2 = mc.snapshot_sync()
        assert snap2["uptime_seconds"] >= snap1["uptime_seconds"]

    def test_empty_snapshot_has_zero_counts(self, mc):
        snap = mc.snapshot_sync()
        assert snap["counters"] == {}
        for hist in snap["histograms"].values():
            assert hist["count"] == 0


# ---------------------------------------------------------------------------
# Concurrency safety (lightweight)
# ---------------------------------------------------------------------------


class TestConcurrencySafety:
    def test_concurrent_increments(self, mc):
        """100 concurrent inc calls should all be counted."""
        async def _run():
            await asyncio.gather(*[mc.inc("concurrent") for _ in range(100)])

        run(_run())
        snap = mc.snapshot_sync()
        assert snap["counters"]["concurrent"] == 100

    def test_concurrent_histogram_records(self, mc):
        async def _run():
            await asyncio.gather(*[mc.record("tool_latency_ms", float(i)) for i in range(50)])

        run(_run())
        snap = mc.snapshot_sync()
        assert snap["histograms"]["tool_latency_ms"]["count"] == 50


# ---------------------------------------------------------------------------
# Singleton test
# ---------------------------------------------------------------------------


def test_get_metrics_singleton():
    a = get_metrics()
    b = get_metrics()
    assert a is b, "get_metrics() should always return the same instance"
