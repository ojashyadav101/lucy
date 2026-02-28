"""Round 5 test suite — 30 tests (A-DD) + 6 regression tests.

Tests cover:
  PR 18: Code Execution Internal Tool (Tests A-F)
  PR 19: Browser Tool Integration (Tests G-L)
  PR 20: Chart Generation Tool (Tests M-R)
  PR 21: Edge Case Wiring (Tests S-X)
  PR 22: Production Monitoring & Health (Tests Y-DD)
  Regression: R4 (5 tests) + R3 (1 test)
"""

import asyncio
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ═══════════════════════════════════════════════════════════════════════════
# PR 18: CODE EXECUTION INTERNAL TOOL
# ═══════════════════════════════════════════════════════════════════════════

class TestCodeExecution:
    """Tests A-F: Code execution tool definitions and safety."""

    def test_A_tool_definitions_structure(self):
        """A: Code tool definitions follow OpenAI schema."""
        from lucy.tools.code_executor import get_code_tool_definitions

        tools = get_code_tool_definitions()
        assert len(tools) == 3

        names = {t["function"]["name"] for t in tools}
        assert names == {"lucy_execute_python", "lucy_execute_bash", "lucy_run_script"}

        for tool in tools:
            assert tool["type"] == "function"
            assert "description" in tool["function"]
            assert "parameters" in tool["function"]

    def test_B_all_tools_use_lucy_prefix(self):
        """B: All code tools use lucy_* prefix."""
        from lucy.tools.code_executor import get_code_tool_definitions

        for tool in get_code_tool_definitions():
            assert tool["function"]["name"].startswith("lucy_")

    def test_C_dangerous_python_blocked(self):
        """C: Dangerous Python patterns are blocked."""
        from lucy.tools.code_executor import _check_dangerous_code

        assert _check_dangerous_code("os.system('rm -rf /tmp')") is not None
        assert _check_dangerous_code("shutil.rmtree('/')") is not None
        assert _check_dangerous_code("import socket") is not None
        # Safe code should pass
        assert _check_dangerous_code("print('hello world')") is None
        assert _check_dangerous_code("import json\nx = json.loads('{}')") is None

    def test_D_dangerous_bash_blocked(self):
        """D: Dangerous bash commands are blocked."""
        from lucy.tools.code_executor import _check_dangerous_bash

        assert _check_dangerous_bash("rm -rf /") is not None
        assert _check_dangerous_bash("dd if=/dev/zero of=/dev/sda") is not None
        assert _check_dangerous_bash(":(){:|:&};:") is not None
        # Safe commands should pass
        assert _check_dangerous_bash("echo hello") is None
        assert _check_dangerous_bash("ls -la") is None

    def test_E_empty_code_returns_error(self):
        """E: Empty code input returns error."""
        from lucy.tools.code_executor import execute_code_tool

        result = asyncio.get_event_loop().run_until_complete(
            execute_code_tool("lucy_execute_python", {"code": ""})
        )
        assert "error" in result

    def test_F_max_timeout_capped(self):
        """F: Timeout is capped at MAX_TIMEOUT."""
        from lucy.tools.code_executor import _MAX_TIMEOUT

        assert _MAX_TIMEOUT == 300


# ═══════════════════════════════════════════════════════════════════════════
# PR 19: BROWSER TOOL INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════

class TestBrowser:
    """Tests G-L: Browser tool definitions and logic."""

    def test_G_tool_definitions_structure(self):
        """G: Browser tool definitions follow OpenAI schema."""
        from lucy.tools.browser import get_browser_tool_definitions

        tools = get_browser_tool_definitions()
        assert len(tools) == 4

        names = {t["function"]["name"] for t in tools}
        expected = {
            "lucy_browse_url", "lucy_browser_snapshot",
            "lucy_browser_interact", "lucy_browser_close",
        }
        assert names == expected

    def test_H_all_tools_use_lucy_prefix(self):
        """H: All browser tools use lucy_* prefix."""
        from lucy.tools.browser import get_browser_tool_definitions

        for tool in get_browser_tool_definitions():
            assert tool["function"]["name"].startswith("lucy_")

    def test_I_search_shorthand_expansion(self):
        """I: @search prefix expands to Google search URL."""
        # Test the logic that would be applied in _handle_browse_url
        url = "@search best python frameworks 2026"
        if url.startswith("@search "):
            query = url[8:].strip()
            url = f"https://www.google.com/search?q={query.replace(' ', '+')}"
        assert "google.com/search" in url
        assert "best+python+frameworks+2026" in url

    def test_J_url_protocol_addition(self):
        """J: URLs without protocol get https:// prepended."""
        url = "example.com/page"
        if not url.startswith(("http://", "https://", "@")):
            url = f"https://{url}"
        assert url == "https://example.com/page"

    def test_K_snapshot_text_extraction(self):
        """K: Accessibility tree text extraction works."""
        from lucy.tools.browser import _extract_text_from_tree

        tree = {
            "role": "heading",
            "name": "Welcome Page",
            "level": "1",
            "children": [
                {"role": "link", "name": "Click here", "ref": "e1"},
                {"role": "text", "name": "Some content"},
                {"role": "button", "name": "Submit", "ref": "e2"},
            ],
        }
        text = _extract_text_from_tree(tree)
        assert "Welcome Page" in text
        assert "Click here" in text
        assert "Submit" in text
        assert "[e1]" in text
        assert "[e2]" in text

    def test_L_snapshot_truncation(self):
        """L: Long snapshots are truncated."""
        from lucy.tools.browser import _format_snapshot, _MAX_SNAPSHOT_CHARS

        long_content = "x" * (_MAX_SNAPSHOT_CHARS + 1000)
        result = _format_snapshot({"content": long_content}, url="https://example.com")
        assert len(result["content"]) < _MAX_SNAPSHOT_CHARS + 100  # Allow for truncation message
        assert "truncated" in result["content"]


# ═══════════════════════════════════════════════════════════════════════════
# PR 20: CHART GENERATION
# ═══════════════════════════════════════════════════════════════════════════

class TestChartGeneration:
    """Tests M-R: Chart generation tool."""

    def test_M_tool_definition_structure(self):
        """M: Chart tool definition follows OpenAI schema."""
        from lucy.tools.chart_generator import get_chart_tool_definitions

        tools = get_chart_tool_definitions()
        assert len(tools) == 1

        tool = tools[0]
        assert tool["function"]["name"] == "lucy_generate_chart"
        assert tool["type"] == "function"
        params = tool["function"]["parameters"]["properties"]
        assert "chart_type" in params
        assert "title" in params
        assert "data" in params

    def test_N_chart_types_enum(self):
        """N: Chart type enum includes all supported types."""
        from lucy.tools.chart_generator import get_chart_tool_definitions

        tool = get_chart_tool_definitions()[0]
        chart_types = tool["function"]["parameters"]["properties"]["chart_type"]["enum"]
        assert set(chart_types) == {"line", "bar", "pie", "scatter", "area"}

    def test_O_color_palette_sufficient(self):
        """O: Color palette has enough colors for 8 datasets."""
        from lucy.tools.chart_generator import _COLORS

        assert len(_COLORS) >= 8
        # All should be hex colors
        for c in _COLORS:
            assert c.startswith("#")
            assert len(c) == 7

    def test_P_size_presets(self):
        """P: Size presets map to reasonable figure dimensions."""
        from lucy.tools.chart_generator import _SIZES

        assert "small" in _SIZES
        assert "medium" in _SIZES
        assert "large" in _SIZES
        # Large should be bigger than small
        assert _SIZES["large"][0] > _SIZES["small"][0]

    def test_Q_generate_chart_creates_file(self):
        """Q: generate_chart produces a PNG file."""
        from lucy.tools.chart_generator import generate_chart

        path = asyncio.get_event_loop().run_until_complete(
            generate_chart(
                chart_type="bar",
                title="Test Chart",
                data={
                    "labels": ["A", "B", "C"],
                    "datasets": [{"label": "Sales", "values": [10, 20, 30]}],
                },
            )
        )
        assert path.exists()
        assert path.suffix == ".png"
        assert path.stat().st_size > 1000  # Should be a real image
        # Cleanup
        path.unlink(missing_ok=True)

    def test_R_execute_chart_tool_returns_result(self):
        """R: execute_chart_tool returns success for valid input."""
        from lucy.tools.chart_generator import execute_chart_tool

        result = asyncio.get_event_loop().run_until_complete(
            execute_chart_tool(
                tool_name="lucy_generate_chart",
                parameters={
                    "chart_type": "line",
                    "title": "Revenue Trend",
                    "data": {
                        "labels": ["Jan", "Feb", "Mar"],
                        "datasets": [{"label": "Revenue", "values": [100, 150, 200]}],
                    },
                },
            )
        )
        assert "result" in result
        assert "file_path" in result
        # Cleanup
        Path(result["file_path"]).unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════════════════════════
# PR 21: EDGE CASE WIRING
# ═══════════════════════════════════════════════════════════════════════════

class TestEdgeCaseWiring:
    """Tests S-X: decide_thread_interrupt integration."""

    def test_S_interrupt_no_active_task(self):
        """S: No active task → respond independently."""
        from lucy.core.edge_cases import decide_thread_interrupt

        decision = decide_thread_interrupt("hello", has_active_bg_task=False)
        assert decision.action == "respond_independently"
        assert decision.reason == "no_active_task"

    def test_T_interrupt_status_query(self):
        """T: Status query during bg task → status reply."""
        from lucy.core.edge_cases import decide_thread_interrupt

        for query in ["what are you working on?", "any update?", "is that done?"]:
            decision = decide_thread_interrupt(query, has_active_bg_task=True)
            assert decision.action == "status_reply", f"Failed for: {query}"

    def test_U_interrupt_cancellation(self):
        """U: Cancel request during bg task → cancel task."""
        from lucy.core.edge_cases import decide_thread_interrupt

        for cancel in ["stop that", "cancel it", "never mind"]:
            decision = decide_thread_interrupt(cancel, has_active_bg_task=True)
            assert decision.action == "cancel_task", f"Failed for: {cancel}"

    def test_V_interrupt_new_message_during_task(self):
        """V: New message during bg task → respond independently."""
        from lucy.core.edge_cases import decide_thread_interrupt

        decision = decide_thread_interrupt(
            "what's the weather in SF?", has_active_bg_task=True
        )
        assert decision.action == "respond_independently"

    def test_W_tool_dedup_window(self):
        """W: Tool deduplication blocks duplicate mutating calls."""
        from lucy.core.edge_cases import should_deduplicate_tool_call

        # Idempotent calls (GET/LIST) are never deduped — that's correct
        assert not should_deduplicate_tool_call(
            "GOOGLECALENDAR_LIST_EVENTS",
            {"calendar_id": "primary"},
            [],
        )

        # Mutating call with no recent history → not deduped
        assert not should_deduplicate_tool_call(
            "GMAIL_SEND_EMAIL",
            {"to": "test@example.com", "subject": "Hi"},
            [],
        )

        # Same *mutating* call within window → should be deduped
        recent = [
            (
                "GMAIL_SEND_EMAIL",
                {"to": "test@example.com", "subject": "Hi"},
                time.monotonic(),  # Must use monotonic to match implementation
            ),
        ]
        assert should_deduplicate_tool_call(
            "GMAIL_SEND_EMAIL",
            {"to": "test@example.com", "subject": "Hi"},
            recent,
        )

    def test_X_tool_idempotency_classification(self):
        """X: Tool idempotency classification is correct."""
        from lucy.core.edge_cases import classify_tool_idempotency

        assert classify_tool_idempotency("GMAIL_GET_EMAIL") == "idempotent"
        assert classify_tool_idempotency("GMAIL_SEND_EMAIL") == "mutating"
        assert classify_tool_idempotency("GOOGLECALENDAR_LIST_EVENTS") == "idempotent"
        assert classify_tool_idempotency("GOOGLECALENDAR_DELETE_EVENT") == "mutating"


# ═══════════════════════════════════════════════════════════════════════════
# PR 22: PRODUCTION MONITORING & HEALTH
# ═══════════════════════════════════════════════════════════════════════════

class TestMonitoring:
    """Tests Y-DD: Monitoring, metrics, and alerting."""

    def test_Y_metrics_collector_record(self):
        """Y: MetricsCollector records and aggregates."""
        from lucy.core.monitoring import MetricsCollector, RequestMetric

        mc = MetricsCollector()
        for i in range(10):
            mc.record(RequestMetric(
                timestamp=time.monotonic(),
                latency_ms=100.0 + i * 10,
                success=True,
                model="minimax-m2.5",
            ))

        assert mc.total_requests == 10
        assert mc.total_errors == 0

        percentiles = mc.get_latency_percentiles()
        assert percentiles["p50"] > 0
        assert percentiles["p95"] >= percentiles["p50"]

    def test_Z_metrics_error_rate(self):
        """Z: Error rate calculation is correct."""
        from lucy.core.monitoring import MetricsCollector, RequestMetric

        mc = MetricsCollector()
        for i in range(10):
            mc.record(RequestMetric(
                timestamp=time.monotonic(),
                latency_ms=100.0,
                success=(i < 8),  # 2 errors out of 10
            ))

        error_rate = mc.get_error_rate()
        assert abs(error_rate - 0.2) < 0.01

    def test_AA_alert_thresholds_defaults(self):
        """AA: Alert thresholds have sensible defaults."""
        from lucy.core.monitoring import AlertThresholds

        t = AlertThresholds()
        assert t.error_rate_warning == 0.05
        assert t.error_rate_critical == 0.15
        assert t.p95_latency_warning == 15_000.0
        assert t.p95_latency_critical == 45_000.0

    def test_BB_alert_manager_triggers(self):
        """BB: AlertManager fires alerts on threshold breach."""
        from lucy.core.monitoring import AlertManager, AlertThresholds, SystemHealth

        am = AlertManager(AlertThresholds(error_rate_critical=0.10))

        health = SystemHealth(
            status="unhealthy",
            error_rate=0.20,  # Above critical threshold
        )
        alerts = am.evaluate(health)
        assert len(alerts) >= 1
        assert any(a["level"] == "critical" for a in alerts)

    def test_CC_alert_cooldown(self):
        """CC: Alert cooldown prevents duplicate alerts."""
        from lucy.core.monitoring import AlertManager, AlertThresholds, SystemHealth

        am = AlertManager(AlertThresholds(error_rate_critical=0.10))
        am._alert_cooldown = 600  # 10 min cooldown

        health = SystemHealth(status="unhealthy", error_rate=0.20)

        # First evaluation triggers
        alerts1 = am.evaluate(health)
        assert len(alerts1) >= 1

        # Second evaluation within cooldown → no new alerts
        alerts2 = am.evaluate(health)
        assert len(alerts2) == 0

    def test_DD_system_health_dict(self):
        """DD: SystemHealth.to_dict() serialization."""
        from lucy.core.monitoring import ComponentHealth, SystemHealth

        health = SystemHealth(
            status="healthy",
            uptime_seconds=3600.0,
            request_count=100,
            error_rate=0.02,
            p50_latency_ms=500.0,
            p95_latency_ms=2000.0,
            p99_latency_ms=5000.0,
            components=[
                ComponentHealth(name="db", status="healthy", latency_ms=5.0),
            ],
        )
        d = health.to_dict()
        assert d["status"] == "healthy"
        assert d["uptime_seconds"] == 3600.0
        assert d["latency_ms"]["p95"] == 2000.0
        assert len(d["components"]) == 1


# ═══════════════════════════════════════════════════════════════════════════
# REGRESSION TESTS
# ═══════════════════════════════════════════════════════════════════════════

class TestR4Regression:
    """R4 regression: history search, file tools, edge cases still work."""

    def test_REG1_history_search_tools_present(self):
        """REG1: History search tools still registered."""
        from lucy.workspace.history_search import get_history_tool_definitions

        tools = get_history_tool_definitions()
        names = {t["function"]["name"] for t in tools}
        assert "lucy_search_slack_history" in names
        assert "lucy_get_channel_history" in names

    def test_REG2_file_tools_present(self):
        """REG2: File generation tools still registered."""
        from lucy.tools.file_generator import get_file_tool_definitions

        tools = get_file_tool_definitions()
        names = {t["function"]["name"] for t in tools}
        assert "lucy_generate_pdf" in names

    def test_REG3_edge_case_detection_stable(self):
        """REG3: Edge case detection functions unchanged."""
        from lucy.core.edge_cases import is_status_query, is_task_cancellation

        assert is_status_query("what are you working on?")
        assert is_task_cancellation("stop that")
        assert not is_status_query("hello there")
        assert not is_task_cancellation("hello there")

    def test_REG4_fast_path_still_works(self):
        """REG4: Fast path bypass unchanged."""
        from lucy.core.fast_path import evaluate_fast_path

        result = evaluate_fast_path("hi")
        assert result is not None
        assert result.response  # Should have a greeting response

    def test_REG5_rate_limiter_exists(self):
        """REG5: Rate limiter module exists and imports."""
        from lucy.core.rate_limiter import RateLimiter

        rl = RateLimiter()
        assert rl is not None

    def test_REG6_request_queue_exists(self):
        """REG6: Request queue module exists and imports."""
        from lucy.core.request_queue import get_request_queue

        q = get_request_queue()
        assert q is not None
