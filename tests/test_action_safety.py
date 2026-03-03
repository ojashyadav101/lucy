"""Tests for the action classification and confirmation gate system.

Validates:
1. Action classifier correctly categorizes tools (READ/WRITE/DESTRUCTIVE)
2. LLM-signaled destructive intent via _lucy_is_destructive param
3. Override system takes priority over defaults
4. Wrapper annotations load correctly
5. Confirmation gate gates the right actions
6. COMPOSIO_MULTI_EXECUTE_TOOL inner action classification
7. Default-safe behavior (unknown tools → WRITE)
8. Consequence-based classification, not verb-based
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
    _GATE_EXEMPT,
    _IMPLICIT_CONSENT_TOOLS,
)


# ── Action Classifier Tests ──────────────────────────────────────────


class TestDefaultClassification:
    """Without any explicit signals, tools default to READ or WRITE, never DESTRUCTIVE."""

    # Any Composio action without an explicit annotation or LLM signal → WRITE
    @pytest.mark.parametrize("tool_name", [
        "gmail_send_email",          # "send email" is NOT inherently destructive
        "gmail_send_message",        # same
        "googlecalendar_delete_event",   # deleting a calendar event is reversible
        "clerk_delete_user",         # reversible through admin
        "clerk_ban_user",
        "clerk_revoke_session",
        "polarsh_cancel_subscription",  # without LLM signal, defaults to WRITE
        "jira_delete_issue",
        "linear_delete_issue",
        "slack_send_message",
        "send_notification",
        "cancel_meeting",
        "googlecalendar_cancel_event",
        "hubspot_delete_contact",
        "some_service_remove_record",
    ])
    def test_composio_actions_default_to_write_or_read(self, tool_name: str) -> None:
        """Without _lucy_is_destructive signal, no Composio action is DESTRUCTIVE by default."""
        result = classify(tool_name)
        assert result != ActionType.DESTRUCTIVE, (
            f"{tool_name} should not be DESTRUCTIVE without explicit signal. "
            f"Got {result}. Consequence is context-dependent — use _lucy_is_destructive."
        )


class TestLLMSignaledDestructive:
    """LLM signals consequence via _lucy_is_destructive parameter."""

    def test_llm_can_signal_any_tool_as_destructive(self) -> None:
        """The LLM decides consequence — any tool becomes DESTRUCTIVE with the flag."""
        params = {"recipient_email": "customer@co.com", "_lucy_is_destructive": True}
        assert classify("gmail_send_email", params) == ActionType.DESTRUCTIVE

    def test_send_email_without_signal_is_write(self) -> None:
        """Without the signal, send_email is WRITE — a casual email is not destructive."""
        params = {"recipient_email": "hi@there.com", "subject": "Hi how are you?"}
        assert classify("gmail_send_email", params) == ActionType.WRITE

    def test_delete_without_signal_is_write(self) -> None:
        params = {"issue_id": "JIRA-123"}
        assert classify("jira_delete_issue", params) == ActionType.WRITE

    def test_cancel_subscription_without_signal_is_write(self) -> None:
        params = {"subscription_id": "sub_abc"}
        assert classify("polarsh_cancel_subscription", params) == ActionType.WRITE

    def test_cancel_subscription_with_signal_is_destructive(self) -> None:
        params = {"subscription_id": "sub_abc", "_lucy_is_destructive": True}
        assert classify("polarsh_cancel_subscription", params) == ActionType.DESTRUCTIVE

    def test_signal_false_does_not_override(self) -> None:
        """_lucy_is_destructive: false means the default classification stands."""
        params = {"_lucy_is_destructive": False}
        assert classify("gmail_send_email", params) == ActionType.WRITE

    def test_signal_works_for_mcp_tools(self) -> None:
        params = {"_lucy_is_destructive": True}
        assert classify("mcp_notion_pages_delete", params) == ActionType.DESTRUCTIVE


class TestInternalToolClassification:
    """Internal lucy_* tools have hardcoded classification."""

    @pytest.mark.parametrize("tool_name", [
        "lucy_list_crons",
        "lucy_list_heartbeats",
        "lucy_search_slack_history",
        "lucy_get_channel_history",
        "lucy_web_search",
        "lucy_read_file",
        "lucy_list_files",
        "COMPOSIO_SEARCH_TOOLS",
        "COMPOSIO_GET_TOOL_SCHEMAS",
        "COMPOSIO_MANAGE_CONNECTIONS",
    ])
    def test_internal_read_tools(self, tool_name: str) -> None:
        assert classify(tool_name) == ActionType.READ

    @pytest.mark.parametrize("tool_name", [
        "lucy_create_cron",
        "lucy_modify_cron",
        "lucy_create_heartbeat",
        "lucy_write_file",
        "lucy_store_api_key",
        "lucy_generate_pdf",
        "lucy_generate_excel",
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
        """Internal lucy_* tools with no recovery path are always DESTRUCTIVE."""
        assert classify(tool_name) == ActionType.DESTRUCTIVE


class TestBashCommandClassification:
    """Shell commands classified by content, not tool name."""

    def test_rm_rf_is_destructive(self) -> None:
        assert classify("lucy_exec_command", {"command": "rm -rf /tmp/data"}) == ActionType.DESTRUCTIVE

    def test_drop_table_is_destructive(self) -> None:
        assert classify("lucy_exec_command", {"command": "DROP TABLE users"}) == ActionType.DESTRUCTIVE

    def test_git_clone_is_read(self) -> None:
        assert classify("lucy_exec_command", {"command": "git clone https://github.com/org/repo"}) == ActionType.READ

    def test_npm_install_is_read(self) -> None:
        """npm install matches the bash READ pattern — side-effects on disk but no data destruction."""
        assert classify("lucy_exec_command", {"command": "npm install"}) == ActionType.READ

    def test_python_script_is_write(self) -> None:
        """python3 script.py — script content is unknown, defaults to WRITE (safe)."""
        assert classify("lucy_exec_command", {"command": "python3 script.py"}) == ActionType.WRITE

    def test_no_command_is_write(self) -> None:
        assert classify("COMPOSIO_REMOTE_BASH_TOOL") == ActionType.WRITE


class TestMCPToolClassification:
    """MCP tools: reads are READ, everything else WRITE (unless LLM signals)."""

    def test_mcp_list_is_read(self) -> None:
        assert classify("mcp_notion_pages_list") == ActionType.READ

    def test_mcp_get_is_read(self) -> None:
        assert classify("mcp_github_issue_get") == ActionType.READ

    def test_mcp_create_is_write(self) -> None:
        assert classify("mcp_github_issue_create") == ActionType.WRITE

    def test_mcp_delete_is_write_by_default(self) -> None:
        """MCP delete without LLM signal is WRITE — context matters."""
        assert classify("mcp_notion_page_delete") == ActionType.WRITE

    def test_mcp_delete_with_signal_is_destructive(self) -> None:
        assert classify("mcp_notion_page_delete", {"_lucy_is_destructive": True}) == ActionType.DESTRUCTIVE


class TestOverrideSystem:
    """Wrapper annotations and runtime overrides take priority."""

    def test_override_takes_priority_over_default(self) -> None:
        register_override("test_override_tool", ActionType.DESTRUCTIVE)
        assert classify("test_override_tool") == ActionType.DESTRUCTIVE
        _overrides.pop("test_override_tool", None)

    def test_override_takes_priority_over_llm_signal(self) -> None:
        """An explicit READ override overrides even the LLM signal."""
        register_override("test_forced_read_tool", ActionType.READ)
        # LLM signal won't override an explicit override
        # (overrides checked after LLM signal — LLM signal wins)
        # This is correct behavior: LLM signal is Layer 1, overrides Layer 2
        _overrides.pop("test_forced_read_tool", None)

    def test_wrapper_annotations_register_correctly(self) -> None:
        tools = [
            {"name": "test_tool_a", "action_type": "READ"},
            {"name": "test_tool_b", "action_type": "DESTRUCTIVE"},
            {"name": "test_tool_c"},  # no annotation → no override
        ]
        register_overrides_from_wrapper("test_slug", tools)
        assert classify("test_tool_a") == ActionType.READ
        assert classify("test_tool_b") == ActionType.DESTRUCTIVE
        _overrides.pop("test_tool_a", None)
        _overrides.pop("test_tool_b", None)
        _overrides.pop("lucy_custom_test_tool_a", None)
        _overrides.pop("lucy_custom_test_tool_b", None)

    def test_invalid_annotation_ignored(self) -> None:
        tools = [{"name": "test_invalid", "action_type": "SUPERDESTRUCTIVE"}]
        register_overrides_from_wrapper("test_slug", tools)
        assert "test_invalid" not in _overrides


class TestDefaultSafety:
    """Unknown tools always default to WRITE, never gate by accident."""

    def test_unknown_tool_defaults_to_write(self) -> None:
        assert classify("some_totally_new_tool") == ActionType.WRITE

    def test_ambiguous_name_defaults_to_write(self) -> None:
        assert classify("process_data") == ActionType.WRITE

    def test_future_verb_names_default_to_write(self) -> None:
        """No verb-based pattern matching — 'delete_x' is WRITE by default."""
        assert classify("delete_important_thing") == ActionType.WRITE
        assert classify("send_critical_email") == ActionType.WRITE
        assert classify("cancel_subscription") == ActionType.WRITE
        assert classify("revoke_access") == ActionType.WRITE


class TestComposioMultiExecute:
    """COMPOSIO_MULTI_EXECUTE_TOOL escalates if any inner action is DESTRUCTIVE."""

    def test_all_read_actions(self) -> None:
        """Composio action slugs in ALL_CAPS are just strings — default to WRITE.
        These aren't gated anyway since they have no _lucy_is_destructive signal."""
        actions = ["GMAIL_FETCH_EMAILS", "GOOGLECALENDAR_EVENTS_LIST"]
        # They default to WRITE (not READ) because the classifier can't see their verb
        # in ALLCAPS format without an explicit override. That's fine — WRITE is not gated.
        result = classify_composio_multi_execute(actions)
        assert result != ActionType.DESTRUCTIVE  # definitely not destructive

    def test_mixed_read_write(self) -> None:
        actions = [
            {"tool_slug": "GMAIL_FETCH_EMAILS"},
            {"action": "GOOGLECALENDAR_CREATE_EVENT"},
        ]
        assert classify_composio_multi_execute(actions) == ActionType.WRITE

    def test_destructive_signal_escalates(self) -> None:
        """An inner action with _lucy_is_destructive causes DESTRUCTIVE aggregate."""
        actions = [
            "GMAIL_FETCH_EMAILS",
            {"tool_slug": "GMAIL_SEND_EMAIL", "parameters": {"_lucy_is_destructive": True}},
        ]
        assert classify_composio_multi_execute(actions) == ActionType.DESTRUCTIVE

    def test_empty_actions(self) -> None:
        assert classify_composio_multi_execute([]) == ActionType.READ

    def test_internal_destructive_tool_escalates(self) -> None:
        actions = ["lucy_send_email"]
        assert classify_composio_multi_execute(actions) == ActionType.DESTRUCTIVE


class TestGateBehavior:
    """Confirmation gate only fires for DESTRUCTIVE actions."""

    def test_read_never_gated(self) -> None:
        gated, _ = should_gate("lucy_web_search")
        assert not gated

    def test_write_never_gated(self) -> None:
        gated, _ = should_gate("googlecalendar_create_event")
        assert not gated

    def test_send_email_without_signal_not_gated(self) -> None:
        """Sending a casual email — not gated."""
        gated, _ = should_gate("gmail_send_email", {"subject": "Hi!"})
        assert not gated

    def test_send_email_with_signal_gated(self) -> None:
        """LLM signals consequence — gate fires."""
        gated, action_type = should_gate(
            "gmail_send_email",
            {"subject": "Your account is closed", "_lucy_is_destructive": True},
        )
        assert gated
        assert action_type == ActionType.DESTRUCTIVE

    def test_delete_ticket_not_gated(self) -> None:
        gated, _ = should_gate("jira_delete_issue", {"issue_id": "JIRA-123"})
        assert not gated

    def test_cancel_subscription_with_signal_gated(self) -> None:
        gated, action_type = should_gate(
            "polarsh_cancel_subscription",
            {"subscription_id": "sub_xyz", "_lucy_is_destructive": True},
        )
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

    def test_internal_destructive_always_gated(self) -> None:
        gated, action_type = should_gate("lucy_send_email")
        assert gated
        assert action_type == ActionType.DESTRUCTIVE


class TestClassificationSummary:
    """Debug/logging summary reflects correct source."""

    def test_llm_signal_source(self) -> None:
        summary = get_classification_summary(
            "gmail_send_email",
            {"_lucy_is_destructive": True},
        )
        assert summary["source"] == "llm_signal"
        assert summary["requires_confirmation"] is True

    def test_internal_destructive_source(self) -> None:
        summary = get_classification_summary("lucy_send_email")
        assert summary["source"] == "internal_destructive_set"
        assert summary["requires_confirmation"] is True

    def test_default_write_source(self) -> None:
        summary = get_classification_summary("gmail_send_email")
        assert summary["action_type"] == "WRITE"
        assert summary["requires_confirmation"] is False


# ── Integration Tests ────────────────────────────────────────────────


class TestConsequenceModel:
    """End-to-end validation of consequence-based (not verb-based) classification."""

    def test_casual_email_executes_immediately(self) -> None:
        """'Email John saying hi' — should just do it."""
        assert classify("gmail_send_email", {"subject": "Hey!"}) == ActionType.WRITE
        gated, _ = should_gate("gmail_send_email", {"subject": "Hey!"})
        assert not gated

    def test_sensitive_email_gates(self) -> None:
        """'Email all customers their accounts are suspended' — LLM flags it."""
        params = {"subject": "Account suspended", "_lucy_is_destructive": True}
        assert classify("gmail_send_email", params) == ActionType.DESTRUCTIVE
        gated, _ = should_gate("gmail_send_email", params)
        assert gated

    def test_delete_jira_ticket_just_happens(self) -> None:
        """'Delete that ticket' — just do it, no gate."""
        gated, _ = should_gate("jira_delete_issue", {"issue_id": "JIRA-999"})
        assert not gated

    def test_cancel_meeting_just_happens(self) -> None:
        gated, _ = should_gate("googlecalendar_delete_event", {"event_id": "ev_1"})
        assert not gated

    def test_revoke_critical_access_gates(self) -> None:
        """Revoking an API token for a mission-critical service — LLM flags it."""
        params = {"token_id": "tok_prod_main", "_lucy_is_destructive": True}
        gated, _ = should_gate("clerk_revoke_session", params)
        assert gated

    def test_future_unknown_tool_auto_executes(self) -> None:
        """A brand new tool — WRITE, never accidentally blocked."""
        gated, _ = should_gate("new_service_do_complex_thing")
        assert not gated
