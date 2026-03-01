"""Tests for the action classification and confirmation gate system.

Validates:
1. Action classifier correctly categorizes tools (READ/WRITE/DESTRUCTIVE)
2. Heuristic patterns match expected tool names
3. Override system takes priority over heuristics
4. Wrapper annotations load correctly
5. Confirmation gate gates the right actions
6. COMPOSIO_MULTI_EXECUTE_TOOL inner action classification
7. Default-safe behavior (unknown tools → WRITE)
"""

import pytest

from lucy.core.action_classifier import (
    ActionType,
    classify,
    classify_composio_multi_execute,
    get_classification_summary,
    register_override,
    register_overrides_from_wrapper,
    _overrides,
)
from lucy.core.confirmation_gate import (
    should_gate,
    format_confirmation_message,
    create_gated_result,
    _GATE_EXEMPT,
    _IMPLICIT_CONSENT_TOOLS,
)


# ── Action Classifier Tests ──────────────────────────────────────────


class TestHeuristicClassification:
    """Test heuristic pattern matching on tool names."""

    # READ tools
    @pytest.mark.parametrize("tool_name", [
        "gmail_fetch_emails",
        "gmail_get_thread",
        "gmail_get_profile",
        "googlecalendar_list_events",
        "googlecalendar_find_free_slots",
        "clerk_list_users",
        "clerk_get_user",
        "clerk_get_user_stats",
        "clerk_list_organizations",
        "clerk_list_sessions",
        "polarsh_list_products",
        "polarsh_get_product",
        "polarsh_list_subscriptions",
        "polarsh_list_customers",
        "polarsh_get_order",
        "polarsh_list_orders",
    ])
    def test_read_tools(self, tool_name: str) -> None:
        assert classify(tool_name) == ActionType.READ, (
            f"{tool_name} should be READ"
        )

    # WRITE tools
    @pytest.mark.parametrize("tool_name", [
        "gmail_create_draft",
        "googlecalendar_create_event",
        "googlecalendar_quick_add",
        "googlecalendar_update_event",
        "clerk_create_user",
        "clerk_update_user",
        "clerk_create_organization",
        "clerk_update_organization",
        "polarsh_create_product",
        "polarsh_update_product",
        "polarsh_create_subscription",
        "polarsh_create_customer",
        "polarsh_create_checkout_link",
        "polarsh_create_benefit",
        "polarsh_create_discount",
    ])
    def test_write_tools(self, tool_name: str) -> None:
        assert classify(tool_name) == ActionType.WRITE, (
            f"{tool_name} should be WRITE"
        )

    # DESTRUCTIVE tools
    @pytest.mark.parametrize("tool_name", [
        "gmail_send_email",
        "gmail_reply_to_thread",
        "googlecalendar_delete_event",
        "clerk_delete_user",
        "clerk_ban_user",
        "clerk_revoke_session",
        "clerk_delete_organization",
        "clerk_delete_email_address",
        "polarsh_delete_customer",
        "polarsh_revoke_subscription",
        "polarsh_delete_benefit",
        "polarsh_delete_discount",
        "polarsh_delete_webhook_endpoint",
    ])
    def test_destructive_tools(self, tool_name: str) -> None:
        assert classify(tool_name) == ActionType.DESTRUCTIVE, (
            f"{tool_name} should be DESTRUCTIVE"
        )


class TestInternalToolClassification:
    """Test classification of internal lucy_* tools."""

    @pytest.mark.parametrize("tool_name", [
        "lucy_list_crons",
        "lucy_list_heartbeats",
        "lucy_search_slack_history",
        "lucy_get_channel_history",
        "lucy_web_search",
    ])
    def test_internal_read_tools(self, tool_name: str) -> None:
        assert classify(tool_name) == ActionType.READ

    @pytest.mark.parametrize("tool_name", [
        "lucy_create_cron",
        "lucy_modify_cron",
        "lucy_create_heartbeat",
        "lucy_write_file",
        "lucy_store_api_key",
    ])
    def test_internal_write_tools(self, tool_name: str) -> None:
        assert classify(tool_name) == ActionType.WRITE

    @pytest.mark.parametrize("tool_name", [
        "lucy_delete_cron",
        "lucy_delete_heartbeat",
        "lucy_delete_custom_integration",
        "lucy_send_email",
    ])
    def test_internal_destructive_tools(self, tool_name: str) -> None:
        assert classify(tool_name) == ActionType.DESTRUCTIVE


class TestComposioToolClassification:
    """Test classification of Composio meta-tools."""

    def test_search_tools_is_read(self) -> None:
        assert classify("COMPOSIO_SEARCH_TOOLS") == ActionType.READ

    def test_get_schemas_is_read(self) -> None:
        assert classify("COMPOSIO_GET_TOOL_SCHEMAS") == ActionType.READ

    def test_manage_connections_is_read(self) -> None:
        assert classify("COMPOSIO_MANAGE_CONNECTIONS") == ActionType.READ

    def test_multi_execute_default_is_write(self) -> None:
        """COMPOSIO_MULTI_EXECUTE_TOOL defaults to WRITE without params."""
        assert classify("COMPOSIO_MULTI_EXECUTE_TOOL") == ActionType.WRITE

    def test_remote_bash_is_write(self) -> None:
        assert classify("COMPOSIO_REMOTE_BASH_TOOL") == ActionType.WRITE


class TestPrefixHandling:
    """Test that lucy_custom_ prefix is properly stripped for classification."""

    def test_custom_prefix_stripped(self) -> None:
        """lucy_custom_gmail_fetch_emails should classify as READ."""
        assert classify("lucy_custom_gmail_fetch_emails") == ActionType.READ

    def test_custom_send_is_destructive(self) -> None:
        assert classify("lucy_custom_gmail_send_email") == ActionType.DESTRUCTIVE

    def test_custom_create_is_write(self) -> None:
        assert classify("lucy_custom_googlecalendar_create_event") == ActionType.WRITE


class TestDefaultSafety:
    """Test that unknown tools default to WRITE (safe default)."""

    def test_unknown_tool_defaults_to_write(self) -> None:
        assert classify("some_totally_new_tool") == ActionType.WRITE

    def test_ambiguous_tool_defaults_to_write(self) -> None:
        assert classify("process_data") == ActionType.WRITE


class TestOverrideSystem:
    """Test the override registration system."""

    def test_override_takes_priority(self) -> None:
        # gmail_fetch_emails would be READ by heuristic
        register_override("test_override_read", ActionType.DESTRUCTIVE)
        assert classify("test_override_read") == ActionType.DESTRUCTIVE
        # Clean up
        _overrides.pop("test_override_read", None)

    def test_wrapper_annotations(self) -> None:
        tools = [
            {"name": "test_tool_a", "action_type": "READ"},
            {"name": "test_tool_b", "action_type": "DESTRUCTIVE"},
            {"name": "test_tool_c"},  # no annotation
        ]
        register_overrides_from_wrapper("test_slug", tools)
        assert classify("test_tool_a") == ActionType.READ
        assert classify("test_tool_b") == ActionType.DESTRUCTIVE
        # Clean up
        _overrides.pop("test_tool_a", None)
        _overrides.pop("test_tool_b", None)
        _overrides.pop("lucy_custom_test_tool_a", None)
        _overrides.pop("lucy_custom_test_tool_b", None)

    def test_invalid_annotation_ignored(self) -> None:
        tools = [{"name": "test_invalid", "action_type": "SUPERDESTRUCTIVE"}]
        register_overrides_from_wrapper("test_slug", tools)
        # Should not be in overrides
        assert "test_invalid" not in _overrides


class TestComposioMultiExecute:
    """Test classification of inner actions in COMPOSIO_MULTI_EXECUTE_TOOL."""

    def test_all_read_actions(self) -> None:
        actions = ["GMAIL_FETCH_EMAILS", "GOOGLECALENDAR_EVENTS_LIST"]
        assert classify_composio_multi_execute(actions) == ActionType.READ

    def test_mixed_read_write(self) -> None:
        actions = [
            {"tool_slug": "GMAIL_FETCH_EMAILS"},
            {"action": "GOOGLECALENDAR_CREATE_EVENT"},
        ]
        assert classify_composio_multi_execute(actions) == ActionType.WRITE

    def test_any_destructive_escalates(self) -> None:
        actions = [
            "GMAIL_FETCH_EMAILS",
            "GMAIL_SEND_EMAIL",
        ]
        assert classify_composio_multi_execute(actions) == ActionType.DESTRUCTIVE

    def test_empty_actions(self) -> None:
        assert classify_composio_multi_execute([]) == ActionType.READ


class TestClassificationSummary:
    """Test the debug/logging summary."""

    def test_summary_includes_all_fields(self) -> None:
        summary = get_classification_summary("gmail_send_email")
        assert "tool_name" in summary
        assert "action_type" in summary
        assert "source" in summary
        assert "requires_confirmation" in summary
        assert summary["action_type"] == "DESTRUCTIVE"
        assert summary["requires_confirmation"] is True

    def test_read_summary_no_confirmation(self) -> None:
        summary = get_classification_summary("gmail_fetch_emails")
        assert summary["action_type"] == "READ"
        assert summary["requires_confirmation"] is False


# ── Confirmation Gate Tests ──────────────────────────────────────────


class TestShouldGate:
    """Test the should_gate decision function."""

    def test_read_never_gated(self) -> None:
        gated, action_type = should_gate("gmail_fetch_emails")
        assert not gated
        assert action_type == ActionType.READ

    def test_write_gated_in_interactive(self) -> None:
        gated, action_type = should_gate("googlecalendar_create_event")
        assert gated
        assert action_type == ActionType.WRITE

    def test_destructive_always_gated(self) -> None:
        gated, action_type = should_gate("gmail_send_email")
        assert gated
        assert action_type == ActionType.DESTRUCTIVE

    def test_exempt_tools_never_gated(self) -> None:
        for tool in _GATE_EXEMPT:
            gated, _ = should_gate(tool)
            assert not gated, f"{tool} should be exempt from gating"

    def test_implicit_consent_never_gated(self) -> None:
        for tool in _IMPLICIT_CONSENT_TOOLS:
            gated, _ = should_gate(tool)
            assert not gated, f"{tool} should have implicit consent"

    def test_cron_automates_write(self) -> None:
        gated, action_type = should_gate(
            "googlecalendar_create_event",
            is_cron_execution=True,
        )
        assert not gated  # WRITE auto-approved in cron
        assert action_type == ActionType.WRITE

    def test_cron_still_gates_destructive(self) -> None:
        gated, action_type = should_gate(
            "gmail_send_email",
            is_cron_execution=True,
        )
        assert gated  # DESTRUCTIVE still gated in cron
        assert action_type == ActionType.DESTRUCTIVE

    def test_composio_multi_execute_inspects_inner(self) -> None:
        params = {"actions": ["GMAIL_SEND_EMAIL"]}
        gated, action_type = should_gate(
            "COMPOSIO_MULTI_EXECUTE_TOOL", params,
        )
        assert gated
        assert action_type == ActionType.DESTRUCTIVE


class TestFormatConfirmation:
    """Test human-readable confirmation messages."""

    def test_destructive_has_warning(self) -> None:
        msg = format_confirmation_message(
            "gmail_send_email",
            {"recipient_email": "test@example.com", "subject": "Hello"},
            ActionType.DESTRUCTIVE,
        )
        assert "⚠️" in msg
        assert "cannot be undone" in msg
        assert "test@example.com" in msg

    def test_write_has_details(self) -> None:
        msg = format_confirmation_message(
            "googlecalendar_create_event",
            {"title": "Meeting", "start_datetime": "2026-03-01T10:00:00"},
            ActionType.WRITE,
        )
        assert "confirmation" in msg.lower()
        assert "Meeting" in msg

    def test_email_params_summarized(self) -> None:
        msg = format_confirmation_message(
            "gmail_send_email",
            {
                "recipient_email": "alice@example.com",
                "subject": "Project Update",
                "body": "Here's the latest...",
            },
            ActionType.DESTRUCTIVE,
        )
        assert "alice@example.com" in msg
        assert "Project Update" in msg


class TestGatedResult:
    """Test the create_gated_result output structure."""

    def test_gated_result_structure(self) -> None:
        # This test would need a mock for create_pending_action
        # For now, test the format_confirmation_message part
        msg = format_confirmation_message(
            "clerk_delete_user",
            {"user_id": "usr_12345"},
            ActionType.DESTRUCTIVE,
        )
        assert "usr_12345" in msg
        assert "⚠️" in msg


# ── Integration Tests ────────────────────────────────────────────────


class TestEndToEnd:
    """End-to-end classification tests for real tool flows."""

    def test_email_draft_flow(self) -> None:
        """User asks to draft email → WRITE, not DESTRUCTIVE."""
        action_type = classify("gmail_create_draft")
        assert action_type == ActionType.WRITE

    def test_email_send_flow(self) -> None:
        """User asks to send email → DESTRUCTIVE."""
        action_type = classify("gmail_send_email")
        assert action_type == ActionType.DESTRUCTIVE

    def test_calendar_check_flow(self) -> None:
        """User checks calendar → READ, no gate."""
        action_type = classify("googlecalendar_list_events")
        assert action_type == ActionType.READ
        gated, _ = should_gate("googlecalendar_list_events")
        assert not gated

    def test_calendar_create_flow(self) -> None:
        """User creates event → WRITE, gated."""
        action_type = classify("googlecalendar_create_event")
        assert action_type == ActionType.WRITE
        gated, _ = should_gate("googlecalendar_create_event")
        assert gated

    def test_user_delete_flow(self) -> None:
        """Admin deletes user → DESTRUCTIVE, gated."""
        action_type = classify("clerk_delete_user")
        assert action_type == ActionType.DESTRUCTIVE
        gated, _ = should_gate("clerk_delete_user")
        assert gated

    def test_custom_prefixed_tools(self) -> None:
        """Custom wrapper tools should be classified the same."""
        assert classify("lucy_custom_gmail_send_email") == ActionType.DESTRUCTIVE
        assert classify("lucy_custom_clerk_list_users") == ActionType.READ
        assert classify("lucy_custom_googlecalendar_create_event") == ActionType.WRITE

    def test_future_unknown_tool_is_safe(self) -> None:
        """A brand new tool with no pattern match → WRITE (safe default)."""
        action_type = classify("some_new_integration_do_thing")
        assert action_type == ActionType.WRITE
        gated, _ = should_gate("some_new_integration_do_thing")
        assert gated
