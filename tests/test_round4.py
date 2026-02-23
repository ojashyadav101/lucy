"""Round 4 test suite — 20 tests across all 4 PRs.

Run: pytest tests/test_round4.py -v
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# ═══════════════════════════════════════════════════════════════════════════
# PR 14: Slack History Search (Tests A-E)
# ═══════════════════════════════════════════════════════════════════════════


class TestSlackHistorySearch:
    """Test the Slack history search module."""

    def setup_method(self):
        from lucy.workspace.history_search import SearchResult
        self.SearchResult = SearchResult

    # --- Test A: search_slack_history finds matching messages ---
    @pytest.mark.asyncio
    async def test_a_search_finds_matches(self, tmp_path):
        """Search finds messages containing the query term."""
        from lucy.workspace.filesystem import WorkspaceFS
        from lucy.workspace.history_search import search_slack_history

        ws = WorkspaceFS("test-ws", tmp_path)
        # Create synced log files
        logs_dir = ws.root / "slack_logs" / "general"
        logs_dir.mkdir(parents=True)
        (logs_dir / "2026-02-23.md").write_text(
            "[10:30:00] <U123> Let's discuss the pricing update\n"
            "[10:31:00] <U456> I agree, pricing needs review\n"
            "[10:32:00] <U123> The weather is nice today\n"
        )

        results = await search_slack_history(ws, "pricing")
        assert len(results) == 2
        assert all("pricing" in r.text.lower() for r in results)
        assert results[0].channel == "general"
        assert results[0].date == "2026-02-23"

    # --- Test B: channel filter works ---
    @pytest.mark.asyncio
    async def test_b_channel_filter(self, tmp_path):
        """Search with channel filter only searches that channel."""
        from lucy.workspace.filesystem import WorkspaceFS
        from lucy.workspace.history_search import search_slack_history

        ws = WorkspaceFS("test-ws", tmp_path)
        for ch in ("general", "engineering"):
            d = ws.root / "slack_logs" / ch
            d.mkdir(parents=True)
            (d / "2026-02-23.md").write_text(
                f"[10:00:00] <U123> test message in {ch}\n"
            )

        results = await search_slack_history(ws, "test message", channel="engineering")
        assert len(results) == 1
        assert results[0].channel == "engineering"

    # --- Test C: days_back limits search range ---
    @pytest.mark.asyncio
    async def test_c_days_back_filter(self, tmp_path):
        """days_back parameter limits how far back to search."""
        from lucy.workspace.filesystem import WorkspaceFS
        from lucy.workspace.history_search import search_slack_history

        ws = WorkspaceFS("test-ws", tmp_path)
        d = ws.root / "slack_logs" / "general"
        d.mkdir(parents=True)
        # Today's messages
        (d / "2026-02-23.md").write_text("[10:00:00] <U123> recent update\n")
        # Very old messages
        (d / "2025-01-01.md").write_text("[10:00:00] <U123> old update\n")

        results = await search_slack_history(ws, "update", days_back=30)
        assert len(results) == 1  # Only today's
        assert results[0].date == "2026-02-23"

    # --- Test D: format_search_results groups by channel ---
    def test_d_format_results(self):
        """format_search_results groups by channel and includes headers."""
        from lucy.workspace.history_search import SearchResult, format_search_results

        results = [
            SearchResult("general", "2026-02-23", "10:00:00", "U123", "hello world", 1),
            SearchResult("engineering", "2026-02-23", "10:01:00", "U456", "hello engineers", 1),
        ]
        formatted = format_search_results(results)
        assert "#general" in formatted
        assert "#engineering" in formatted
        assert "2 messages" in formatted

    # --- Test E: tool definitions are valid OpenAI format ---
    def test_e_tool_definitions(self):
        """get_history_tool_definitions returns valid OpenAI tool schemas."""
        from lucy.workspace.history_search import get_history_tool_definitions

        tools = get_history_tool_definitions()
        assert len(tools) == 2
        for tool in tools:
            assert tool["type"] == "function"
            assert "name" in tool["function"]
            assert "parameters" in tool["function"]
            assert tool["function"]["name"].startswith("lucy_")


# ═══════════════════════════════════════════════════════════════════════════
# PR 15: System Prompt Audit (Tests F-J)
# ═══════════════════════════════════════════════════════════════════════════


class TestSystemPromptAudit:
    """Test that prompt updates are present and well-formed."""

    def _read_prompt(self, filename: str) -> str:
        here = Path(__file__).parent.parent / "assets" / filename
        return here.read_text(encoding="utf-8")

    # --- Test F: investigation discipline enforcement ---
    def test_f_investigation_depth(self):
        """SYSTEM_PROMPT.md includes 3-source verification rule."""
        prompt = self._read_prompt("SYSTEM_PROMPT.md")
        assert "3 independent sources" in prompt.lower() or "3 independent" in prompt.lower()

    # --- Test G: draft-review-iterate cycle ---
    def test_g_draft_review_iterate(self):
        """SYSTEM_PROMPT.md includes the review cycle guidance."""
        prompt = self._read_prompt("SYSTEM_PROMPT.md")
        assert "draft" in prompt.lower()
        # Should have review/iterate language
        assert "re-read critically" in prompt.lower() or "review" in prompt.lower()

    # --- Test H: proactive intelligence section ---
    def test_h_proactive_intelligence(self):
        """SYSTEM_PROMPT.md includes proactive intelligence section."""
        prompt = self._read_prompt("SYSTEM_PROMPT.md")
        assert "proactive" in prompt.lower()
        assert "anticipate" in prompt.lower() or "pattern recognition" in prompt.lower()

    # --- Test I: background task patterns in SOUL.md ---
    def test_i_background_task_patterns(self):
        """SOUL.md includes handling for background tasks."""
        soul = self._read_prompt("SOUL.md")
        assert "background task" in soul.lower()
        assert "status" in soul.lower()

    # --- Test J: anti-patterns preserved ---
    def test_j_anti_patterns_preserved(self):
        """SOUL.md still contains anti-patterns section."""
        soul = self._read_prompt("SOUL.md")
        assert "anti-pattern" in soul.lower()


# ═══════════════════════════════════════════════════════════════════════════
# PR 16: File Output Tools (Tests K-O)
# ═══════════════════════════════════════════════════════════════════════════


class TestFileOutputTools:
    """Test file generation capabilities."""

    # --- Test K: CSV generation ---
    @pytest.mark.asyncio
    async def test_k_csv_generation(self):
        """generate_csv produces a valid CSV file."""
        from lucy.tools.file_generator import generate_csv

        path = await generate_csv(
            title="Test Report",
            rows=[["Name", "Value"], ["Alice", 100], ["Bob", 200]],
        )
        assert path.exists()
        assert path.suffix == ".csv"
        content = path.read_text()
        assert "Name,Value" in content
        assert "Alice,100" in content

    # --- Test L: Excel generation (if openpyxl available) ---
    @pytest.mark.asyncio
    async def test_l_excel_generation(self):
        """generate_excel produces a valid .xlsx file."""
        try:
            from lucy.tools.file_generator import generate_excel
        except ImportError:
            pytest.skip("openpyxl not installed")

        path = await generate_excel(
            title="Test Workbook",
            sheets={"Sheet1": [["Col A", "Col B"], [1, 2], [3, 4]]},
        )
        assert path.exists()
        assert path.suffix == ".xlsx"
        assert path.stat().st_size > 0

    # --- Test M: tool definitions for file tools ---
    def test_m_file_tool_definitions(self):
        """get_file_tool_definitions returns valid schemas."""
        from lucy.tools.file_generator import get_file_tool_definitions

        tools = get_file_tool_definitions()
        assert len(tools) == 3
        names = {t["function"]["name"] for t in tools}
        assert "lucy_generate_pdf" in names
        assert "lucy_generate_excel" in names
        assert "lucy_generate_csv" in names

    # --- Test N: execute_file_tool dispatches correctly ---
    @pytest.mark.asyncio
    async def test_n_execute_csv_tool(self):
        """execute_file_tool handles CSV generation."""
        from lucy.tools.file_generator import execute_file_tool

        result = await execute_file_tool(
            tool_name="lucy_generate_csv",
            parameters={
                "title": "Test",
                "rows": [["A", "B"], [1, 2]],
            },
        )
        assert "result" in result
        assert "csv" in result["result"].lower() or "file_path" in result

    # --- Test O: unknown file tool returns error ---
    @pytest.mark.asyncio
    async def test_o_unknown_file_tool(self):
        """execute_file_tool returns error for unknown tools."""
        from lucy.tools.file_generator import execute_file_tool

        result = await execute_file_tool(
            tool_name="lucy_generate_unknown",
            parameters={},
        )
        assert "error" in result


# ═══════════════════════════════════════════════════════════════════════════
# PR 17: Edge Case Handlers (Tests P-T)
# ═══════════════════════════════════════════════════════════════════════════


class TestEdgeCaseHandlers:
    """Test edge case handling for concurrent/background scenarios."""

    # --- Test P: status query detection ---
    def test_p_status_query_detection(self):
        """is_status_query detects various status patterns."""
        from lucy.core.edge_cases import is_status_query

        assert is_status_query("what are you working on?")
        assert is_status_query("are you busy?")
        assert is_status_query("how's that going?")
        assert is_status_query("any update?")
        assert is_status_query("is that done?")
        assert not is_status_query("create a new task")
        assert not is_status_query("hello!")

    # --- Test Q: task cancellation detection ---
    def test_q_cancellation_detection(self):
        """is_task_cancellation detects cancellation patterns."""
        from lucy.core.edge_cases import is_task_cancellation

        assert is_task_cancellation("cancel that")
        assert is_task_cancellation("never mind")
        assert is_task_cancellation("stop that task")
        assert is_task_cancellation("don't bother")
        assert is_task_cancellation("scratch that")
        assert not is_task_cancellation("what's the status?")

    # --- Test R: tool idempotency classification ---
    def test_r_tool_idempotency(self):
        """classify_tool_idempotency correctly labels tools."""
        from lucy.core.edge_cases import classify_tool_idempotency

        assert classify_tool_idempotency("GITHUB_GET_REPO") == "idempotent"
        assert classify_tool_idempotency("LINEAR_LIST_ISSUES") == "idempotent"
        assert classify_tool_idempotency("GITHUB_CREATE_ISSUE") == "mutating"
        assert classify_tool_idempotency("SLACK_SEND_MESSAGE") == "mutating"
        assert classify_tool_idempotency("SOME_TOOL") == "unknown"

    # --- Test S: duplicate mutating call blocked ---
    def test_s_duplicate_dedup(self):
        """should_deduplicate_tool_call blocks identical mutating calls."""
        from lucy.core.edge_cases import should_deduplicate_tool_call

        now = time.monotonic()
        recent = [("GITHUB_CREATE_ISSUE", {"title": "Bug"}, now)]

        # Same mutating call within window → blocked
        assert should_deduplicate_tool_call(
            "GITHUB_CREATE_ISSUE", {"title": "Bug"}, recent
        )

        # Same GET call → NOT blocked (idempotent)
        recent_get = [("GITHUB_GET_REPO", {"repo": "x"}, now)]
        assert not should_deduplicate_tool_call(
            "GITHUB_GET_REPO", {"repo": "x"}, recent_get
        )

        # Different params → NOT blocked
        assert not should_deduplicate_tool_call(
            "GITHUB_CREATE_ISSUE", {"title": "Feature"}, recent
        )

    # --- Test T: graceful degradation messages ---
    def test_t_degradation_messages(self):
        """get_degradation_message returns warm messages for each type."""
        from lucy.core.edge_cases import (
            classify_error_for_degradation,
            get_degradation_message,
        )

        # Rate limit
        assert classify_error_for_degradation(Exception("429 Too Many Requests")) == "rate_limited"
        msg = get_degradation_message("rate_limited")
        assert "moment" in msg.lower() or "right now" in msg.lower()

        # Timeout
        assert classify_error_for_degradation(Exception("Request timed out")) == "tool_timeout"
        msg = get_degradation_message("tool_timeout")
        assert "longer" in msg.lower() or "different" in msg.lower()

        # Unknown
        msg = get_degradation_message("some_random_type")
        assert msg  # Should still return something


# ═══════════════════════════════════════════════════════════════════════════
# Regression: Round 3 features still work
# ═══════════════════════════════════════════════════════════════════════════


class TestRound3Regression:
    """Ensure Round 3 features are not broken by Round 4 changes."""

    # --- Regression 1: fast path still works ---
    def test_regression_fast_path(self):
        """Fast path still handles greetings."""
        from lucy.core.fast_path import evaluate_fast_path

        result = evaluate_fast_path("hello!", thread_depth=0)
        assert result.is_fast

    # --- Regression 2: rate limiter still works ---
    def test_regression_rate_limiter(self):
        """Rate limiter still classifies APIs."""
        from lucy.core.rate_limiter import get_rate_limiter

        limiter = get_rate_limiter()
        assert limiter.classify_api_from_tool("GOOGLECALENDAR_LIST_EVENTS", {}) == "google_calendar"

    # --- Regression 3: request queue metrics ---
    def test_regression_request_queue(self):
        """Request queue metrics still accessible."""
        from lucy.core.request_queue import get_request_queue

        q = get_request_queue()
        metrics = q.metrics
        assert "queue_size" in metrics

    # --- Regression 4: router classification ---
    def test_regression_router(self):
        """Router still classifies intents."""
        from lucy.core.router import classify_and_route

        route = classify_and_route("help me write a Python script")
        assert route.tier in ("fast", "default", "code", "frontier")

    # --- Regression 5: reactions still work ---
    def test_regression_reactions(self):
        """Contextual reactions still classify messages."""
        from lucy.slack.reactions import classify_reaction

        r = classify_reaction("help me with code")
        assert r.emoji  # Should return some emoji


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
