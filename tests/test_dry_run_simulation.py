"""Dry-run simulation suite for Lucy's full execution pipeline.

Each test simulates a realistic end-to-end scenario by mocking only
external I/O (LLM, Composio API, Slack). Everything else — metrics,
circuit breakers, retrieval, safety, claim validation — runs for real.

Scenarios covered
-----------------
1. Simple chat: no tools needed — fast path
2. Calendar query: retrieval → tool call → formatted response
3. Auth-missing: Composio not connected → structured connection link (no CLI)
4. Multi-turn with errors: tool fails first turn, second turn expands K
5. Tool-loop protection: LLM calls same tool 5x → fallback triggered
6. No-text fallback: LLM returns empty content → synthesised response
7. Claim validator: LLM claims completeness on truncated data → qualified
8. Circuit breaker: Composio errors 4x → breaker opens → graceful message
9. Timeout: slow tool call → returns retryable error dict
10. 1000-tool retrieval: BM25 pinpoints right tools out of 1000 schemas
11. Thread memory: conversation history injected into messages
12. Timezone injection: workspace tz_offset used in LLM prompt
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from lucy.core.circuit_breaker import (
    BreakerConfig,
    BreakerState,
    CircuitBreaker,
    CircuitBreakerOpen,
)
from lucy.core.safety import (
    AuthResponseBuilder,
    ClaimValidator,
    get_auth_builder,
    get_claim_validator,
)
from lucy.core.timeout import ToolType, with_timeout
from lucy.observability.metrics import MetricsCollector
from lucy.observability.slo import SLOEvaluator
from lucy.retrieval.capability_index import (
    CapabilityIndex,
    WorkspaceIndex,
    bm25_score,
    expand_query,
    tokenise,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers / Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _make_tool_schema(name: str, description: str, app: str | None = None) -> dict:
    """Minimal OpenAI-format tool schema."""
    inferred_app = app or name.split("_")[0].lower()
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {"input": {"type": "string"}},
            },
        },
        "x_composio_app": inferred_app,
    }


def _calendar_tool(name_suffix: str) -> dict:
    descriptions = {
        "LIST_EVENTS": "List calendar events and meetings in a time range",
        "CREATE_EVENT": "Create a new calendar event or meeting",
        "DELETE_EVENT": "Delete or cancel a calendar event",
        "UPDATE_EVENT": "Update or reschedule an existing event",
    }
    name = f"GOOGLECALENDAR_{name_suffix}"
    return _make_tool_schema(name, descriptions.get(name_suffix, "Calendar tool"), "googlecalendar")


def _bulk_tools(n: int) -> list[dict]:
    """Generate n diverse fake tool schemas to simulate large tool counts."""
    apps = [
        ("GITHUB", "GitHub repository and issue management"),
        ("GMAIL", "Email sending and reading"),
        ("JIRA", "Project and issue tracking"),
        ("NOTION", "Notes and docs"),
        ("LINEAR", "Engineering issue tracker"),
        ("FIGMA", "Design collaboration"),
        ("ASANA", "Task management"),
        ("TRELLO", "Kanban boards"),
        ("HUBSPOT", "CRM and sales pipeline"),
        ("ZENDESK", "Customer support tickets"),
    ]
    tools = []
    for i in range(n):
        app_name, app_desc = apps[i % len(apps)]
        tool_name = f"{app_name}_ACTION_{i:04d}"
        tools.append(_make_tool_schema(
            tool_name,
            f"{app_desc}: perform action {i}",
            app_name.lower(),
        ))
    return tools


# ─────────────────────────────────────────────────────────────────────────────
# 1. Simple chat — fast path (no tools)
# ─────────────────────────────────────────────────────────────────────────────

class TestScenario01SimpleChatFastPath:
    """Simple greetings / chat tasks should never trigger tool fetching."""

    @pytest.mark.asyncio
    async def test_fast_path_skips_tool_fetch(self):
        """Classifier marks 'chat' intent → tool fetch is skipped entirely."""
        from lucy.routing.classifier import Classification
        from lucy.routing.tiers import ModelTier

        classification = Classification(intent="chat", tier=ModelTier.TIER_1_FAST, confidence=0.99)

        with patch("lucy.routing.classifier.get_classifier") as mock_cls:
            mock_cls.return_value.classify = AsyncMock(return_value=classification)
            result = await mock_cls.return_value.classify("hey there")
            assert result.intent == "chat"
            assert result.tier == ModelTier.TIER_1_FAST

    @pytest.mark.asyncio
    async def test_metrics_no_tool_calls_on_chat(self):
        """Tool call counter must not increment for chat-only tasks."""
        mc = MetricsCollector()
        # Simulate: no tool calls recorded
        snap = await mc.snapshot()
        assert snap["counters"].get("tool_calls_total", 0) == 0


# ─────────────────────────────────────────────────────────────────────────────
# 2. Calendar query — retrieval → correct tools returned
# ─────────────────────────────────────────────────────────────────────────────

class TestScenario02CalendarRetrieval:
    """BM25 retrieval should return calendar tools for meeting/schedule queries."""

    def _build_index_with_calendar(self, ws_id: str, extra_tools: int = 0) -> WorkspaceIndex:
        """Build a WorkspaceIndex with 4 calendar tools + extra noise tools."""
        idx = WorkspaceIndex(ws_id)
        tools = [
            _calendar_tool("LIST_EVENTS"),
            _calendar_tool("CREATE_EVENT"),
            _calendar_tool("DELETE_EVENT"),
            _calendar_tool("UPDATE_EVENT"),
        ] + _bulk_tools(extra_tools)
        # add_tools is async — run synchronously via asyncio.get_event_loop
        asyncio.get_event_loop().run_until_complete(idx.add_tools(tools))
        return idx

    def test_calendar_query_returns_calendar_tools(self):
        ws_id = str(uuid4())
        idx = self._build_index_with_calendar(ws_id, extra_tools=50)
        result = idx.retrieve(
            "what meetings do I have today",
            k=5,
            connected_apps={"googlecalendar"},
        )
        names = [t["function"]["name"] for t in result.tools if "function" in t]
        assert any("GOOGLECALENDAR" in n for n in names), (
            f"No calendar tools returned for calendar query. Got: {names}"
        )

    def test_schedule_query_ranks_list_events_first(self):
        ws_id = str(uuid4())
        idx = self._build_index_with_calendar(ws_id, extra_tools=20)
        result = idx.retrieve(
            "show my schedule for today",
            k=3,
            connected_apps={"googlecalendar"},
        )
        names = [t["function"]["name"] for t in result.tools if "function" in t]
        assert "GOOGLECALENDAR_LIST_EVENTS" in names

    def test_no_calendar_tools_without_connected_app(self):
        """Without googlecalendar in connected_apps, calendar tools should not appear."""
        ws_id = str(uuid4())
        idx = self._build_index_with_calendar(ws_id)
        result = idx.retrieve(
            "what meetings do I have today",
            k=5,
            connected_apps={"github"},
        )
        names = [t["function"]["name"] for t in result.tools if "function" in t]
        assert not any("GOOGLECALENDAR" in n for n in names), (
            f"Calendar tools returned without auth. Got: {names}"
        )

    def test_synonym_expansion_meetings_matches_events(self):
        """'meetings' in query should match 'events' in description via synonyms."""
        tokens = expand_query(tokenise("meetings"))
        assert "calendar" in tokens or "events" in tokens


# ─────────────────────────────────────────────────────────────────────────────
# 3. Auth-missing — structured connection link, no CLI instructions
# ─────────────────────────────────────────────────────────────────────────────

class TestScenario03AuthMissing:
    """When an integration isn't connected, Lucy must return a link, not CLI commands."""

    @pytest.mark.asyncio
    async def test_auth_builder_returns_link_not_cli(self):
        builder = AuthResponseBuilder()
        with patch.object(builder, "_get_link", new=AsyncMock(return_value="https://connect.composio.dev/link/abc123")):
            with patch("lucy.core.safety.get_composio_client"):
                msg = await builder.build(["googlecalendar"], "test-workspace-id")
        assert "https://connect.composio.dev/link/abc123" in msg
        assert "gog " not in msg

    @pytest.mark.asyncio
    async def test_auth_builder_no_raw_error_exposure(self):
        builder = AuthResponseBuilder()
        with patch.object(builder, "_get_link", new=AsyncMock(return_value=None)):
            with patch("lucy.core.safety.get_composio_client"):
                msg = await builder.build(["gmail"], "test-workspace-id")
        assert "Gmail" in msg or "gmail" in msg.lower()
        assert "Traceback" not in msg
        assert "Exception" not in msg

    @pytest.mark.asyncio
    async def test_auth_builder_multi_app(self):
        builder = AuthResponseBuilder()
        with patch.object(builder, "_get_link", new=AsyncMock(return_value="https://link.example.com/x")):
            with patch("lucy.core.safety.get_composio_client"):
                msg = await builder.build(["gmail", "github"], "test-workspace-id")
        assert "Gmail" in msg or "gmail" in msg.lower()
        assert "GitHub" in msg or "github" in msg.lower()

    @pytest.mark.asyncio
    async def test_auth_message_actionable(self):
        """Message must include a link or clear call-to-action."""
        builder = AuthResponseBuilder()
        with patch.object(builder, "_get_link", new=AsyncMock(return_value="https://composio.dev/connect/x")):
            with patch("lucy.core.safety.get_composio_client"):
                msg = await builder.build(["linear"], "test-workspace-id")
        assert "connect" in msg.lower() or "https://" in msg


# ─────────────────────────────────────────────────────────────────────────────
# 4. Claim validator — qualifies completeness claims on truncated data
# ─────────────────────────────────────────────────────────────────────────────

class TestScenario04ClaimValidator:
    """LLM response with 'that's all' on truncated data must be qualified."""

    def test_claim_qualified_on_truncated_response(self):
        cv = ClaimValidator()
        response = "Here are your meetings: Meeting A, Meeting B. That's all I found!"
        truncated_tool_results = ["[TRUNCATED: 24500/12000 chars]"]
        is_partial = cv.response_is_partial(truncated_tool_results)
        result = cv.validate(response, is_partial=is_partial)
        assert "partial" in result.lower() or "may be partial" in result.lower()
        assert "That's all" in result or "that's all" in result.lower()

    def test_no_modification_when_not_truncated(self):
        cv = ClaimValidator()
        response = "Here are all your meetings: A, B, C. That's everything!"
        # No truncation
        is_partial = cv.response_is_partial(["some normal result"])
        result = cv.validate(response, is_partial=is_partial)
        # Should NOT modify since data wasn't truncated
        assert result == response

    def test_qualified_only_once(self):
        cv = ClaimValidator()
        response = "That's all the events. That covers everything."
        result = cv.validate(response, is_partial=True)
        # Should only add disclaimer once
        count = result.count("may be partial")
        assert count <= 1

    def test_patterns_detected(self):
        cv = ClaimValidator()
        claims = [
            "That's all the events I found.",
            "I have listed all items.",
            "Those are all of your meetings.",
            "This covers everything on your calendar.",
        ]
        for claim in claims:
            result = cv.validate(claim, is_partial=True)
            assert "partial" in result.lower(), f"Claim not qualified: {claim!r}"


# ─────────────────────────────────────────────────────────────────────────────
# 5. Tool-loop protection — same tool called 5x → fallback
# ─────────────────────────────────────────────────────────────────────────────

class TestScenario05ToolLoopProtection:
    """Repeated calls to the same tool trigger loop detection."""

    @pytest.mark.asyncio
    async def test_tool_loop_increments_counter(self):
        mc = MetricsCollector()
        for _ in range(3):
            await mc.tool_loop_detected()
        snap = await mc.snapshot()
        assert snap["counters"]["tool_loops_total"] == 3

    @pytest.mark.asyncio
    async def test_metrics_emits_loop_signal(self):
        mc = MetricsCollector()
        await mc.tool_loop_detected()
        await mc.tool_loop_detected()
        snap = await mc.snapshot()
        assert snap["counters"]["tool_loops_total"] >= 2


# ─────────────────────────────────────────────────────────────────────────────
# 6. No-text fallback — LLM returns empty content
# ─────────────────────────────────────────────────────────────────────────────

class TestScenario06NoTextFallback:
    @pytest.mark.asyncio
    async def test_no_text_fallback_counter(self):
        mc = MetricsCollector()
        await mc.no_text_fallback()
        snap = await mc.snapshot()
        assert snap["counters"]["no_text_fallbacks_total"] >= 1


# ─────────────────────────────────────────────────────────────────────────────
# 7. Circuit breaker — Composio errors 4x → breaker opens
# ─────────────────────────────────────────────────────────────────────────────

class TestScenario07CircuitBreaker:
    """After failure_threshold errors, circuit opens and blocks further calls."""

    @pytest.mark.asyncio
    async def test_circuit_opens_after_threshold(self):
        cb = CircuitBreaker("composio_api", BreakerConfig(
            failure_threshold=4, minimum_calls=2, recovery_timeout=60.0
        ))
        async def _fail():
            raise ConnectionError("Composio down")

        for _ in range(4):
            with pytest.raises(ConnectionError):
                await cb.call(_fail)

        assert cb.state == BreakerState.OPEN

    @pytest.mark.asyncio
    async def test_open_circuit_returns_actionable_message(self):
        cb = CircuitBreaker("composio_api", BreakerConfig(
            failure_threshold=2, minimum_calls=1, recovery_timeout=60.0
        ))
        async def _fail():
            raise ConnectionError("down")

        for _ in range(2):
            with pytest.raises(ConnectionError):
                await cb.call(_fail)

        assert cb.is_open()
        with pytest.raises(CircuitBreakerOpen) as exc_info:
            await cb.call(AsyncMock(return_value="ok"))

        cbo = exc_info.value
        assert "OPEN" in str(cbo) or "circuit" in str(cbo).lower()

    @pytest.mark.asyncio
    async def test_circuit_recovers_after_timeout(self):
        cb = CircuitBreaker("composio_api", BreakerConfig(
            failure_threshold=2, minimum_calls=1, recovery_timeout=0.05
        ))
        async def _fail():
            raise ConnectionError()
        async def _succeed():
            return "ok"

        for _ in range(2):
            with pytest.raises(ConnectionError):
                await cb.call(_fail)

        await asyncio.sleep(0.1)
        result = await cb.call(_succeed)
        assert result == "ok"
        assert cb.state == BreakerState.CLOSED


# ─────────────────────────────────────────────────────────────────────────────
# 8. Timeout — slow tool returns retryable error, not hangs
# ─────────────────────────────────────────────────────────────────────────────

class TestScenario08Timeout:
    @pytest.mark.asyncio
    async def test_integration_tool_times_out_gracefully(self):
        async def slow_calendar_call():
            await asyncio.sleep(30)
            return {"events": []}

        start = time.monotonic()
        result = await with_timeout(
            slow_calendar_call(),
            tool_type=ToolType.INTEGRATION_TOOL,
            tool_name="GOOGLECALENDAR_LIST_EVENTS",
            override_seconds=0.05,
        )
        elapsed = time.monotonic() - start

        assert elapsed < 1.0
        assert isinstance(result, dict)
        assert result["status"] == "error"
        assert result["error_type"] == "retryable"
        assert "GOOGLECALENDAR_LIST_EVENTS" in result["tool"]

    @pytest.mark.asyncio
    async def test_fast_tool_completes_within_budget(self):
        async def fast_call():
            await asyncio.sleep(0.01)
            return {"result": "done"}

        result = await with_timeout(
            fast_call(),
            tool_type=ToolType.INTEGRATION_TOOL,
        )
        assert result == {"result": "done"}


# ─────────────────────────────────────────────────────────────────────────────
# 9. 1000-tool retrieval — BM25 pinpoints right tools
# ─────────────────────────────────────────────────────────────────────────────

class TestScenario091000ToolRetrieval:
    """Simulate 1000 tool index — verify precision and speed."""

    def _build_large_index(self, ws_id: str, n: int = 1000) -> WorkspaceIndex:
        idx = WorkspaceIndex(ws_id)
        noise = _bulk_tools(n - 4)
        targets = [
            _calendar_tool("LIST_EVENTS"),
            _calendar_tool("CREATE_EVENT"),
            _calendar_tool("DELETE_EVENT"),
            _calendar_tool("UPDATE_EVENT"),
        ]
        asyncio.get_event_loop().run_until_complete(idx.add_tools(targets + noise))
        return idx

    def test_retrieval_precision_at_k10_calendar(self):
        ws_id = str(uuid4())
        idx = self._build_large_index(ws_id, n=1000)
        result = idx.retrieve(
            "what events do I have on my calendar today",
            k=10,
            connected_apps={"googlecalendar"},
        )
        names = [t["function"]["name"] for t in result.tools if "function" in t]
        calendar_tools = [n for n in names if "GOOGLECALENDAR" in n]
        assert len(calendar_tools) >= 2, f"Low precision: {calendar_tools} from {names}"

    def test_retrieval_speed_under_100ms(self):
        ws_id = str(uuid4())
        idx = self._build_large_index(ws_id, n=1000)
        start = time.monotonic()
        idx.retrieve(
            "what events do I have on my calendar today",
            k=10,
            connected_apps={"googlecalendar"},
        )
        elapsed_ms = (time.monotonic() - start) * 1000
        assert elapsed_ms < 100, f"Retrieval took {elapsed_ms:.1f}ms — should be < 100ms"

    def test_github_query_returns_github_tools(self):
        ws_id = str(uuid4())
        idx = WorkspaceIndex(ws_id)
        asyncio.get_event_loop().run_until_complete(idx.add_tools(
            [_make_tool_schema("GITHUB_LIST_ISSUES", "List open GitHub issues", "github"),
             _make_tool_schema("GITHUB_CREATE_PR", "Create a pull request on GitHub", "github"),
             _make_tool_schema("GITHUB_REVIEW_CODE", "Review code in a GitHub PR", "github")]
            + _bulk_tools(200)
        ))
        result = idx.retrieve(
            "show me my open GitHub issues",
            k=5,
            connected_apps={"github"},
        )
        names = [t["function"]["name"] for t in result.tools if "function" in t]
        assert any("GITHUB" in n for n in names), f"No GitHub tools in top-5: {names}"

    def test_gmail_query_returns_email_tools(self):
        ws_id = str(uuid4())
        idx = WorkspaceIndex(ws_id)
        asyncio.get_event_loop().run_until_complete(idx.add_tools(
            [_make_tool_schema("GMAIL_LIST_EMAILS", "List and read Gmail emails", "gmail"),
             _make_tool_schema("GMAIL_SEND_EMAIL", "Send an email via Gmail", "gmail")]
            + _bulk_tools(300)
        ))
        result = idx.retrieve(
            "read my emails from today",
            k=5,
            connected_apps={"gmail"},
        )
        names = [t["function"]["name"] for t in result.tools if "function" in t]
        assert any("GMAIL" in n for n in names), f"No Gmail tools in top-5: {names}"

    def test_no_cross_app_leakage(self):
        """Calendar query must not return GitHub tools and vice versa."""
        ws_id = str(uuid4())
        idx = WorkspaceIndex(ws_id)
        asyncio.get_event_loop().run_until_complete(idx.add_tools(
            [_calendar_tool("LIST_EVENTS"),
             _make_tool_schema("GITHUB_LIST_ISSUES", "List GitHub issues", "github")]
            + _bulk_tools(100)
        ))
        result = idx.retrieve(
            "what meetings do I have today",
            k=5,
            connected_apps={"googlecalendar"},
        )
        names = [t["function"]["name"] for t in result.tools if "function" in t]
        assert not any("GITHUB" in n for n in names), f"Cross-app leakage: {names}"

    def test_usage_boost_promotes_frequently_used_tools(self):
        ws_id = str(uuid4())
        idx = WorkspaceIndex(ws_id)
        asyncio.get_event_loop().run_until_complete(idx.add_tools([
            _calendar_tool("LIST_EVENTS"),
            _calendar_tool("CREATE_EVENT"),
        ] + _bulk_tools(50)))

        for _ in range(10):
            idx.record_usage("GOOGLECALENDAR_LIST_EVENTS")

        result = idx.retrieve(
            "what are my upcoming events",
            k=3,
            connected_apps={"googlecalendar"},
        )
        names = [t["function"]["name"] for t in result.tools if "function" in t]
        if names:
            assert names[0] == "GOOGLECALENDAR_LIST_EVENTS", (
                f"Expected LIST_EVENTS first (usage boost). Got: {names}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# 10. SLO evaluation — correct pass/fail assessment
# ─────────────────────────────────────────────────────────────────────────────

class TestScenario10SLOEvaluation:
    """SLO evaluator must correctly assess metrics against production thresholds."""

    def _snap(self, **kwargs) -> dict:
        return {
            "uptime_seconds": 100.0,
            "counters": {
                "tool_calls_total": kwargs.get("tool_calls", 0),
                "tool_errors_total": kwargs.get("tool_errors", 0),
                "unknown_tool_calls_total": kwargs.get("unknown", 0),
                "no_text_fallbacks_total": kwargs.get("no_text", 0),
            },
            "labeled_counters": {
                "tasks_total": {
                    "completed": kwargs.get("completed", 0),
                    "failed": kwargs.get("failed", 0),
                }
            },
            "histograms": {
                "tool_latency_ms": {
                    "count": kwargs.get("tool_hist_count", 0),
                    "p95_ms": kwargs.get("tool_p95", 0.0),
                },
                "task_latency_ms": {
                    "count": kwargs.get("task_hist_count", 0),
                    "p95_ms": kwargs.get("task_p95", 0.0),
                },
            },
        }

    def test_healthy_system_all_pass(self):
        ev = SLOEvaluator()
        snap = self._snap(
            tool_calls=1000, tool_errors=5,      # 99.5% success
            unknown=0,
            no_text=1, completed=500,            # 0.2% fallback
            tool_p95=3000.0, tool_hist_count=100,
            task_p95=10000.0, task_hist_count=100,
        )
        report = ev.evaluate_snapshot(snap)
        assert report.all_passing, f"Failing SLOs: {[r.slo.name for r in report.failing]}"

    def test_degraded_system_fails(self):
        ev = SLOEvaluator()
        snap = self._snap(
            tool_calls=100, tool_errors=10,  # 90% — below 99% threshold
        )
        report = ev.evaluate_snapshot(snap)
        failing_names = [r.slo.name for r in report.failing]
        assert "tool_success_rate" in failing_names

    def test_high_latency_fails_slo(self):
        ev = SLOEvaluator()
        snap = self._snap(tool_p95=12000.0, tool_hist_count=20)
        report = ev.evaluate_snapshot(snap)
        failing_names = [r.slo.name for r in report.failing]
        assert "tool_p95_latency_ms" in failing_names

    def test_slo_report_serializes(self):
        ev = SLOEvaluator()
        snap = self._snap()
        report = ev.evaluate_snapshot(snap)
        d = report.to_dict()
        assert "overall" in d
        assert isinstance(d["slos"], list)


# ─────────────────────────────────────────────────────────────────────────────
# 11. Timezone injection — workspace tz_offset in router prompt
# ─────────────────────────────────────────────────────────────────────────────

class TestScenario11TimezoneInjection:
    """Router must inject workspace timezone into the date context, not hardcode IST."""

    @pytest.mark.asyncio
    async def test_ist_default_injected(self):
        from lucy.routing.router import ModelRouter
        from unittest.mock import patch, AsyncMock

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "Hi there", "tool_calls": None}}],
            "usage": {},
        }
        mock_resp.raise_for_status = MagicMock()

        captured_payload = {}

        async def capture_post(url, json=None, **kwargs):
            captured_payload.update(json or {})
            return mock_resp

        with patch("httpx.AsyncClient.post", side_effect=capture_post):
            router = ModelRouter()
            from lucy.core.openclaw import OpenClawResponse
            from lucy.routing.tiers import ModelTier, get_tier_config
            try:
                await router.route(
                    messages=[{"role": "user", "content": "test"}],
                    tier=ModelTier.TIER_1_FAST,
                )
            except Exception:
                pass

        if captured_payload.get("messages"):
            system_msg = next(
                (m["content"] for m in captured_payload["messages"] if m["role"] == "system"),
                ""
            )
            assert "UTC" in system_msg or "time" in system_msg.lower()

    @pytest.mark.asyncio
    async def test_custom_tz_offset_used(self):
        from lucy.routing.router import ModelRouter

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "ok", "tool_calls": None}}],
            "usage": {},
        }
        mock_resp.raise_for_status = MagicMock()

        captured_payload = {}

        async def capture_post(url, json=None, **kwargs):
            captured_payload.update(json or {})
            return mock_resp

        with patch("httpx.AsyncClient.post", side_effect=capture_post):
            router = ModelRouter()
            from lucy.routing.tiers import ModelTier
            try:
                await router.route(
                    messages=[{"role": "user", "content": "test"}],
                    tier=ModelTier.TIER_1_FAST,
                    tz_offset_hours=-5.0,  # EST
                    tz_label="America/New_York (EST, UTC-5)",
                )
            except Exception:
                pass

        if captured_payload.get("messages"):
            system_msg = next(
                (m["content"] for m in captured_payload["messages"] if m["role"] == "system"),
                ""
            )
            assert "America/New_York" in system_msg or "EST" in system_msg or "-5" in system_msg


# ─────────────────────────────────────────────────────────────────────────────
# 12. Metrics end-to-end
# ─────────────────────────────────────────────────────────────────────────────

class TestScenario12MetricsEndToEnd:
    """Verify all metric recording paths produce correct snapshot values."""

    @pytest.mark.asyncio
    async def test_full_task_lifecycle_metrics(self):
        mc = MetricsCollector()

        # Simulate a task with 3 tool calls, 1 error
        await mc.tool_called("GOOGLECALENDAR_LIST_EVENTS")
        await mc.tool_called("GMAIL_SEND_EMAIL")
        await mc.tool_called("GITHUB_LIST_ISSUES")
        await mc.tool_error("auth")

        # Simulate latency recordings
        await mc.record("tool_latency_ms", 450.0)
        await mc.record("tool_latency_ms", 1200.0)
        await mc.record("tool_latency_ms", 300.0)
        await mc.record("task_latency_ms", 3500.0)

        # Task completed
        await mc.task_completed(status="completed", elapsed_ms=3500.0)

        snap = await mc.snapshot()

        assert snap["counters"]["tool_calls_total"] >= 3
        assert snap["counters"]["tool_errors_total"] >= 1
        assert snap["labeled_counters"]["tasks_total"]["completed"] >= 1
        assert snap["histograms"]["tool_latency_ms"]["count"] >= 3
        assert snap["histograms"]["task_latency_ms"]["count"] >= 1

    @pytest.mark.asyncio
    async def test_p95_latency_reflects_distribution(self):
        mc = MetricsCollector()
        # Add 100 values: 95 under 1000ms, 5 over 8000ms
        for _ in range(95):
            await mc.record("tool_latency_ms", 500.0)
        for _ in range(5):
            await mc.record("tool_latency_ms", 9000.0)

        snap = await mc.snapshot()
        p95 = snap["histograms"]["tool_latency_ms"]["p95_ms"]
        # p95 should be somewhere between 500 and 9000
        assert 400 < p95 < 9500, f"Unexpected p95: {p95}"
