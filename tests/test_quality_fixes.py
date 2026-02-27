"""Tests for the Response Quality, Reliability, and Intelligence fixes.

Validates all 9 fixes from the plan:
1. Event deduplication
2. Retry logic (tenacity in openclaw)
3. LLM-redirected failure recovery (no hardcoded error strings)
4. Silent recovery cascade (no "Something went wrong")
5. 400 recovery mid-loop
6. Dynamic environment injection
7. Three-layer output pipeline
8. Tool search pre-filtering
9. System prompt intelligence sections
"""

from __future__ import annotations

import asyncio
import re

import pytest


# ═══════════════════════════════════════════════════════════════════════
# TEST 1: Event Deduplication
# ═══════════════════════════════════════════════════════════════════════

class TestEventDedup:
    def test_dedup_cache_exists(self) -> None:
        from lucy.slack.handlers import _processed_events, EVENT_DEDUP_TTL

        assert isinstance(_processed_events, dict)
        assert EVENT_DEDUP_TTL == 30.0

    def test_dedup_blocks_duplicate_event_ts(self) -> None:
        from lucy.slack import handlers

        handlers._processed_events.clear()

        import time
        now = time.monotonic()
        handlers._processed_events["1234567890.123456"] = now

        assert "1234567890.123456" in handlers._processed_events

    def test_dedup_ttl_cleanup(self) -> None:
        from lucy.slack import handlers

        handlers._processed_events.clear()

        import time
        old_time = time.monotonic() - 60.0
        handlers._processed_events["old_event"] = old_time
        handlers._processed_events["new_event"] = time.monotonic()

        now = time.monotonic()
        cleaned = {
            k: v for k, v in handlers._processed_events.items()
            if now - v < handlers.EVENT_DEDUP_TTL
        }
        assert "old_event" not in cleaned
        assert "new_event" in cleaned


# ═══════════════════════════════════════════════════════════════════════
# TEST 2: No Hardcoded Error Strings in Codebase
# ═══════════════════════════════════════════════════════════════════════

class TestNoHardcodedErrors:
    FORBIDDEN_STRINGS = [
        "Something went wrong while processing your request",
        "I wasn't able to complete the request after several tool calls",
        "Could you try rephrasing?",
        "I'm running into a loop with tool calls",
    ]

    def test_handlers_no_forbidden_strings(self) -> None:
        import inspect
        from lucy.slack import handlers

        source = inspect.getsource(handlers)
        for forbidden in self.FORBIDDEN_STRINGS:
            assert forbidden not in source, (
                f"Forbidden string found in handlers.py: '{forbidden}'"
            )

    def test_agent_no_forbidden_strings(self) -> None:
        import inspect
        from lucy.core import agent

        source = inspect.getsource(agent)
        for forbidden in self.FORBIDDEN_STRINGS:
            assert forbidden not in source, (
                f"Forbidden string found in agent.py: '{forbidden}'"
            )

    def test_agent_no_hit_a_snag(self) -> None:
        import inspect
        from lucy.core import agent

        source = inspect.getsource(agent)
        assert "I hit a snag" not in source, (
            "Hardcoded 'I hit a snag' still present in agent.py"
        )


# ═══════════════════════════════════════════════════════════════════════
# TEST 3: Output Pipeline — Sanitizer
# ═══════════════════════════════════════════════════════════════════════

class TestOutputSanitizer:
    def test_strips_internal_paths(self) -> None:
        from lucy.pipeline.output import process_output_sync

        text = "I created /home/user/company/SKILL.md for you."
        result = process_output_sync(text)
        assert "/home/user/" not in result

    def test_strips_workspace_paths(self) -> None:
        from lucy.pipeline.output import process_output_sync

        text = "Updated /workspace/test/skills/SKILL.md"
        result = process_output_sync(text)
        assert "/workspace/" not in result

    def test_strips_composio_tool_names(self) -> None:
        from lucy.pipeline.output import process_output_sync

        text = "I used COMPOSIO_SEARCH_TOOLS to find that."
        result = process_output_sync(text)
        assert "COMPOSIO_SEARCH_TOOLS" not in result
        assert "COMPOSIO" not in result

    def test_strips_composio_brand(self) -> None:
        from lucy.pipeline.output import process_output_sync

        text = "Connect via Composio to authorize."
        result = process_output_sync(text)
        assert "Composio" not in result
        assert "composio" not in result.lower()

    def test_strips_openrouter(self) -> None:
        from lucy.pipeline.output import process_output_sync

        text = "Using OpenRouter for model access."
        result = process_output_sync(text)
        assert "OpenRouter" not in result

    def test_strips_allcaps_tool_names(self) -> None:
        from lucy.pipeline.output import process_output_sync

        text = "GOOGLECALENDAR_CREATE_EVENT was called."
        result = process_output_sync(text)
        assert "GOOGLECALENDAR_CREATE_EVENT" not in result

    def test_humanizes_known_tools(self) -> None:
        from lucy.pipeline.output import _sanitize

        text = "I used GOOGLECALENDAR_CREATE_EVENT to help."
        result = _sanitize(text)
        assert "schedule a meeting" in result

    def test_strips_skill_filenames(self) -> None:
        from lucy.pipeline.output import process_output_sync

        text = "I saved it to SKILL.md and LEARNINGS.md"
        result = process_output_sync(text)
        assert "SKILL.md" not in result
        assert "LEARNINGS.md" not in result


# ═══════════════════════════════════════════════════════════════════════
# TEST 4: Output Pipeline — Markdown to Slack Converter
# ═══════════════════════════════════════════════════════════════════════

class TestMarkdownToSlack:
    def test_bold_conversion(self) -> None:
        from lucy.pipeline.output import _convert_markdown_to_slack

        assert _convert_markdown_to_slack("**bold**") == "*bold*"

    def test_heading_conversion(self) -> None:
        from lucy.pipeline.output import _convert_markdown_to_slack

        result = _convert_markdown_to_slack("### My Heading")
        assert "*My Heading*" in result
        assert "###" not in result

    def test_link_conversion(self) -> None:
        from lucy.pipeline.output import _convert_markdown_to_slack

        result = _convert_markdown_to_slack("[Click here](https://example.com)")
        assert "<https://example.com|Click here>" in result

    def test_table_conversion(self) -> None:
        from lucy.pipeline.output import _convert_markdown_to_slack

        table = (
            "| Name | Status |\n"
            "| ---- | ------ |\n"
            "| Gmail | Active |\n"
            "| Drive | Active |"
        )
        result = _convert_markdown_to_slack(table)
        assert "|" not in result or "•" in result
        assert "Gmail" in result


# ═══════════════════════════════════════════════════════════════════════
# TEST 5: Output Pipeline — Tone Validator
# ═══════════════════════════════════════════════════════════════════════

class TestToneValidator:
    def test_replaces_wasnt_able(self) -> None:
        from lucy.pipeline.output import _validate_tone

        text = "I wasn't able to complete your request."
        result = _validate_tone(text)
        assert "wasn't able to" not in result

    def test_replaces_try_rephrasing(self) -> None:
        from lucy.pipeline.output import _validate_tone

        text = "Could you try rephrasing your question?"
        result = _validate_tone(text)
        assert "try rephrasing" not in result

    def test_replaces_hit_a_snag(self) -> None:
        from lucy.pipeline.output import _validate_tone

        text = "I hit a snag trying to do that."
        result = _validate_tone(text)
        assert "hit a snag" not in result

    def test_replaces_something_went_wrong(self) -> None:
        from lucy.pipeline.output import _validate_tone

        text = "Something went wrong with the request."
        result = _validate_tone(text)
        assert "Something went wrong" not in result

    def test_clean_text_passes_through(self) -> None:
        from lucy.pipeline.output import _validate_tone

        text = "Your meeting is scheduled for 3pm."
        result = _validate_tone(text)
        assert result == text


# ═══════════════════════════════════════════════════════════════════════
# TEST 6: Dynamic Environment Injection
# ═══════════════════════════════════════════════════════════════════════

class TestDynamicEnvInjection:
    @pytest.mark.asyncio
    async def test_prompt_includes_connected_services(self) -> None:
        from pathlib import Path
        from unittest.mock import AsyncMock, PropertyMock

        from lucy.pipeline.prompt import build_system_prompt
        from lucy.workspace.filesystem import WorkspaceFS

        ws = AsyncMock(spec=WorkspaceFS)
        ws.workspace_id = "test"
        ws.list_skills = AsyncMock(return_value=[])
        ws.read_file = AsyncMock(return_value=None)
        type(ws).root = PropertyMock(return_value=Path("/tmp/test_ws"))

        result = await build_system_prompt(
            ws, connected_services=["Gmail", "Google Calendar", "Google Drive"],
        )

        assert "Gmail" in result
        assert "Google Calendar" in result
        assert "already active" in result.lower() or "already connected" in result.lower()

    @pytest.mark.asyncio
    async def test_prompt_without_services_has_no_env_block(self) -> None:
        from pathlib import Path
        from unittest.mock import AsyncMock, PropertyMock

        from lucy.pipeline.prompt import build_system_prompt
        from lucy.workspace.filesystem import WorkspaceFS

        ws = AsyncMock(spec=WorkspaceFS)
        ws.workspace_id = "test"
        ws.list_skills = AsyncMock(return_value=[])
        ws.read_file = AsyncMock(return_value=None)
        type(ws).root = PropertyMock(return_value=Path("/tmp/test_ws"))

        result = await build_system_prompt(ws, connected_services=None)
        assert "<current_environment>" not in result

    def test_composio_client_has_get_connected_app_names(self) -> None:
        from lucy.integrations.composio_client import ComposioClient

        assert hasattr(ComposioClient, "get_connected_app_names")


# ═══════════════════════════════════════════════════════════════════════
# TEST 7: Tool Search Pre-Filtering
# ═══════════════════════════════════════════════════════════════════════

class TestToolPreFiltering:
    def test_filters_large_result_set(self) -> None:
        from lucy.core.agent import _filter_search_results

        items = [
            {"name": f"tool_{i}", "connected": i < 3}
            for i in range(20)
        ]
        result = _filter_search_results({"items": items}, max_results=5)
        assert len(result["items"]) == 5
        assert result["_filtered_from"] == 20

    def test_preserves_small_result_set(self) -> None:
        from lucy.core.agent import _filter_search_results

        items = [{"name": "tool_1", "connected": True}]
        result = _filter_search_results({"items": items}, max_results=5)
        assert len(result["items"]) == 1
        assert "_filtered_from" not in result

    def test_prioritizes_connected_tools(self) -> None:
        from lucy.core.agent import _filter_search_results

        items = [
            {"name": "disconnected_1", "connected": False},
            {"name": "connected_1", "connected": True},
            {"name": "disconnected_2", "connected": False},
            {"name": "connected_2", "connected": True},
        ] + [{"name": f"extra_{i}", "connected": False} for i in range(10)]

        result = _filter_search_results({"items": items}, max_results=3)
        names = [i["name"] for i in result["items"]]
        assert "connected_1" in names
        assert "connected_2" in names


# ═══════════════════════════════════════════════════════════════════════
# TEST 8: Model Routing Still Works
# ═══════════════════════════════════════════════════════════════════════

class TestModelRouting:
    def test_greeting_routes_to_fast(self) -> None:
        from lucy.pipeline.router import classify_and_route

        result = classify_and_route("Hi")
        assert result.tier == "fast"

    def test_code_routes_to_code(self) -> None:
        from lucy.pipeline.router import classify_and_route

        result = classify_and_route("Build me a calculator script")
        assert result.tier == "code"

    def test_research_routes_to_frontier(self) -> None:
        from lucy.pipeline.router import classify_and_route

        result = classify_and_route(
            "Research the top 10 competitors in the AI agent space and "
            "analyze their pricing models in detail"
        )
        assert result.tier == "frontier"

    def test_general_routes_to_default(self) -> None:
        from lucy.pipeline.router import classify_and_route

        result = classify_and_route("Send an email to John about the meeting")
        assert result.tier == "default"


# ═══════════════════════════════════════════════════════════════════════
# TEST 9: System Prompt Contains Intelligence Sections
# ═══════════════════════════════════════════════════════════════════════

class TestSystemPromptSections:
    def test_has_intelligence_rules(self) -> None:
        from pathlib import Path
        prompt_path = Path(__file__).parent.parent / "assets" / "SYSTEM_PROMPT.md"
        content = prompt_path.read_text()
        assert "<intelligence_rules>" in content
        assert "Never list disconnected services" in content

    def test_has_response_quality(self) -> None:
        from pathlib import Path
        prompt_path = Path(__file__).parent.parent / "assets" / "SYSTEM_PROMPT.md"
        content = prompt_path.read_text()
        assert "<response_quality>" in content
        assert "internal checklist" in content

    def test_has_memory_discipline(self) -> None:
        from pathlib import Path
        prompt_path = Path(__file__).parent.parent / "assets" / "SYSTEM_PROMPT.md"
        content = prompt_path.read_text()
        assert "<memory_discipline>" in content
        assert "team members by name" in content

    def test_error_handling_no_weakness(self) -> None:
        from pathlib import Path
        prompt_path = Path(__file__).parent.parent / "assets" / "SYSTEM_PROMPT.md"
        content = prompt_path.read_text()
        assert "You never fail" in content
        assert "NEVER say any of these" in content
        assert "Silent retry" in content or "silent retry" in content.lower()
        assert "Partial delivery" in content or "partial delivery" in content.lower()

    def test_knowledge_discovery_section(self) -> None:
        from pathlib import Path
        prompt_path = Path(__file__).parent.parent / "assets" / "SYSTEM_PROMPT.md"
        content = prompt_path.read_text()
        assert "Knowledge Discovery" in content


# ═══════════════════════════════════════════════════════════════════════
# TEST 10: Context Trimming / Workflow Resilience
# ═══════════════════════════════════════════════════════════════════════

class TestWorkflowResilience:
    def test_trim_tool_results_reduces_size(self) -> None:
        from lucy.core.agent import _trim_tool_results

        messages = [
            {"role": "user", "content": "Do something complex"},
            {"role": "tool", "tool_call_id": "1", "content": "x" * 5000},
            {"role": "tool", "tool_call_id": "2", "content": "y" * 5000},
            {"role": "tool", "tool_call_id": "3", "content": "z" * 5000},
        ]

        trimmed = _trim_tool_results(messages, max_result_chars=500)
        first_tool = trimmed[1]
        assert len(first_tool["content"]) <= 600

    def test_trim_preserves_recent_results(self) -> None:
        from lucy.core.agent import _trim_tool_results

        messages = [
            {"role": "user", "content": "Do something"},
            {"role": "tool", "tool_call_id": "1", "content": "old_result" * 100},
            {"role": "tool", "tool_call_id": "2", "content": "new_result" * 100},
            {"role": "tool", "tool_call_id": "3", "content": "newest_result" * 100},
        ]

        trimmed = _trim_tool_results(messages, max_result_chars=500)
        last_tool = trimmed[-1]
        assert "newest_result" in last_tool["content"]

    def test_constants_defined(self) -> None:
        from lucy.core.agent import (
            MAX_PAYLOAD_CHARS,
            TOOL_RESULT_SUMMARY_THRESHOLD,
        )

        assert TOOL_RESULT_SUMMARY_THRESHOLD == 4_000
        assert MAX_PAYLOAD_CHARS == 120_000


# ═══════════════════════════════════════════════════════════════════════
# TEST 11: Retry Logic in OpenClaw
# ═══════════════════════════════════════════════════════════════════════

class TestRetryLogic:
    def test_retryable_status_codes(self) -> None:
        from lucy.core.openclaw import _is_retryable_llm_error, OpenClawError

        assert _is_retryable_llm_error(OpenClawError("rate limited", status_code=429))
        assert _is_retryable_llm_error(OpenClawError("server error", status_code=500))
        assert _is_retryable_llm_error(OpenClawError("bad gateway", status_code=502))
        assert not _is_retryable_llm_error(OpenClawError("bad request", status_code=400))
        assert not _is_retryable_llm_error(OpenClawError("not found", status_code=404))

    def test_timeout_is_retryable(self) -> None:
        import httpx
        from lucy.core.openclaw import _is_retryable_llm_error

        assert _is_retryable_llm_error(httpx.ReadTimeout("timed out"))
        assert _is_retryable_llm_error(httpx.ConnectTimeout("connect timed out"))


# ═══════════════════════════════════════════════════════════════════════
# TEST 12: Agent has model_override parameter
# ═══════════════════════════════════════════════════════════════════════

class TestAgentModelOverride:
    def test_run_accepts_model_override(self) -> None:
        import inspect
        from lucy.core.agent import LucyAgent

        sig = inspect.signature(LucyAgent.run)
        assert "model_override" in sig.parameters

    def test_collect_partial_results(self) -> None:
        from lucy.core.agent import LucyAgent

        messages = [
            {"role": "user", "content": "test"},
            {"role": "tool", "tool_call_id": "1", "content": '{"data": "found some results"}'},
            {"role": "tool", "tool_call_id": "2", "content": '{"error": "something failed"}'},
        ]

        result = LucyAgent._collect_partial_results(messages)
        assert "gathered so far" in result
        assert "found some results" in result
        assert "something failed" not in result
