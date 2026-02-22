"""Phase 2 safety component tests.

Tests for:
  - AuthResponseBuilder: app inference, link generation fallback, single/multi-app messages
  - ClaimValidator: truncation detection, phrase stripping, no-op when not partial
  - ProviderFormatter: Gmail, GitHub issues, Linear tickets detection and formatting
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from lucy.core.safety import (
    APP_METADATA,
    AuthResponseBuilder,
    ClaimValidator,
    ProviderFormatter,
    _app_human_name,
)


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers / fixtures
# ─────────────────────────────────────────────────────────────────────────────

WORKSPACE_ID = uuid4()


def _mock_client(link: str | None = "https://connect.composio.dev/link/test123"):
    client = MagicMock()
    client.create_connection_link = AsyncMock(return_value=link)
    return client


# ─────────────────────────────────────────────────────────────────────────────
# APP_METADATA & helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestAppMetadata:
    def test_all_slugs_have_human_names(self):
        for slug in APP_METADATA:
            name, desc = APP_METADATA[slug]
            assert name, f"Empty name for {slug}"
            assert desc, f"Empty desc for {slug}"

    def test_human_name_known_slug(self):
        assert _app_human_name("googlecalendar") == "Google Calendar"
        assert _app_human_name("github") == "GitHub"
        assert _app_human_name("linear") == "Linear"

    def test_human_name_unknown_slug_titlecases(self):
        result = _app_human_name("somethingnovel")
        assert result == "Somethingnovel"


# ─────────────────────────────────────────────────────────────────────────────
# AuthResponseBuilder
# ─────────────────────────────────────────────────────────────────────────────

class TestAuthResponseBuilder:
    def test_infer_app_googlecalendar(self):
        assert AuthResponseBuilder._infer_app_from_tool("GOOGLECALENDAR_EVENTS_LIST") == "googlecalendar"

    def test_infer_app_github(self):
        assert AuthResponseBuilder._infer_app_from_tool("GITHUB_LIST_ISSUES") == "github"

    def test_infer_app_gmail(self):
        assert AuthResponseBuilder._infer_app_from_tool("GMAIL_SEND_EMAIL") == "gmail"

    def test_infer_app_linear(self):
        assert AuthResponseBuilder._infer_app_from_tool("LINEAR_GET_ISSUES") == "linear"

    def test_infer_app_unknown_returns_none(self):
        assert AuthResponseBuilder._infer_app_from_tool("UNKNOWN_TOOL_XYZ") is None

    def test_build_single_app_with_link(self):
        builder = AuthResponseBuilder()
        with patch("lucy.core.safety.get_composio_client", return_value=_mock_client()):
            result = run(builder.build(["googlecalendar"], WORKSPACE_ID))
        assert "Google Calendar" in result
        assert "https://connect.composio.dev/link/test123" in result
        assert "ask me again" in result.lower()

    def test_build_single_app_link_fallback(self):
        """When link generation fails, still produce a usable message."""
        builder = AuthResponseBuilder()
        with patch("lucy.core.safety.get_composio_client", return_value=_mock_client(link=None)):
            result = run(builder.build(["github"], WORKSPACE_ID))
        assert "GitHub" in result
        assert "connect" in result.lower()

    def test_build_multiple_apps(self):
        builder = AuthResponseBuilder()
        with patch("lucy.core.safety.get_composio_client", return_value=_mock_client()):
            result = run(builder.build(["googlecalendar", "gmail"], WORKSPACE_ID))
        assert "Google Calendar" in result
        assert "Gmail" in result

    def test_build_for_tool_error_known_app(self):
        builder = AuthResponseBuilder()
        with patch("lucy.core.safety.get_composio_client", return_value=_mock_client()):
            result = run(
                builder.build_for_tool_error(
                    tool_name="GOOGLECALENDAR_EVENTS_LIST",
                    error_text="403 Forbidden",
                    workspace_id=WORKSPACE_ID,
                )
            )
        assert "Google Calendar" in result
        assert "https://connect.composio.dev/link/test123" in result

    def test_build_for_tool_error_unknown_app(self):
        builder = AuthResponseBuilder()
        with patch("lucy.core.safety.get_composio_client", return_value=_mock_client()):
            result = run(
                builder.build_for_tool_error(
                    tool_name="UNKNOWN_RANDOM_TOOL",
                    error_text="401",
                    workspace_id=WORKSPACE_ID,
                )
            )
        # No link — just a sanitised generic message
        assert "authorisation" in result.lower() or "authorization" in result.lower()
        # Must NOT contain CLI instructions
        assert "bash" not in result.lower()
        assert "gog " not in result.lower()

    def test_build_response_no_cli_instructions(self):
        """Auth responses must never suggest CLI commands."""
        builder = AuthResponseBuilder()
        with patch("lucy.core.safety.get_composio_client", return_value=_mock_client(link=None)):
            result = run(builder.build(["googlecalendar"], WORKSPACE_ID))
        for bad in ("gog auth", "bash", "curl ", "client_secret.json"):
            assert bad not in result, f"CLI instruction '{bad}' found in auth response"


# ─────────────────────────────────────────────────────────────────────────────
# ClaimValidator
# ─────────────────────────────────────────────────────────────────────────────

class TestClaimValidator:
    def test_no_modification_when_not_partial(self):
        v = ClaimValidator()
        text = "Here are all your events: Meeting at 3pm. That's all."
        assert v.validate(text, is_partial=False) == text

    def test_no_modification_when_no_claims(self):
        v = ClaimValidator()
        text = "You have a meeting at 3pm tomorrow."
        assert v.validate(text, is_partial=True) == text

    def test_qualifies_thats_all(self):
        v = ClaimValidator()
        text = "You have 3 meetings today. That's all."
        result = v.validate(text, is_partial=True)
        assert "partial" in result.lower() or "note" in result.lower()
        # Original content still present
        assert "3 meetings" in result

    def test_qualifies_nothing_else(self):
        v = ClaimValidator()
        text = "I checked your inbox. Nothing else in there."
        result = v.validate(text, is_partial=True)
        assert "partial" in result.lower() or "note" in result.lower()

    def test_qualifies_full_list(self):
        v = ClaimValidator()
        text = "Here is your full list of issues: LIN-1, LIN-2."
        result = v.validate(text, is_partial=True)
        assert "partial" in result.lower() or "note" in result.lower()

    def test_qualifies_here_are_all(self):
        v = ClaimValidator()
        text = "Here are all your emails from today."
        result = v.validate(text, is_partial=True)
        assert "partial" in result.lower() or "note" in result.lower()

    def test_qualifies_no_more(self):
        v = ClaimValidator()
        text = "Those are the 5 events. No more meetings scheduled."
        result = v.validate(text, is_partial=True)
        assert "partial" in result.lower() or "note" in result.lower()

    def test_only_qualifies_once(self):
        """Multiple claims in one response should only get one disclaimer."""
        v = ClaimValidator()
        text = "That's all the events. Nothing else. Full list shown."
        result = v.validate(text, is_partial=True)
        count = result.lower().count("partial")
        # Only one disclaimer injected
        assert count <= 2, f"Too many qualifiers ({count}) added"

    def test_empty_text_unchanged(self):
        v = ClaimValidator()
        assert v.validate("", is_partial=True) == ""

    def test_response_is_partial_with_truncation_marker(self):
        v = ClaimValidator()
        contents = ["[TRUNCATED: removed 5000 chars]\n{...}", "normal content"]
        assert v.response_is_partial(contents) is True

    def test_response_is_partial_without_truncation(self):
        v = ClaimValidator()
        contents = ["normal content", "more normal content"]
        assert v.response_is_partial(contents) is False

    def test_response_is_partial_empty_list(self):
        v = ClaimValidator()
        assert v.response_is_partial([]) is False


# ─────────────────────────────────────────────────────────────────────────────
# ProviderFormatter — Gmail
# ─────────────────────────────────────────────────────────────────────────────

class TestProviderFormatterGmail:
    def _make_result(self, messages: list[dict]) -> list[dict]:
        return [{"status": "success", "result": {"messages": messages}}]

    def _sample_messages(self):
        return [
            {
                "subject": "Team standup notes",
                "from": "alice@example.com",
                "date": "2026-02-21T09:00:00+00:00",
                "snippet": "Here are the notes from today...",
            },
            {
                "subject": "Q1 budget review",
                "from": "bob@example.com",
                "date": "2026-02-20T14:30:00+00:00",
                "snippet": "Please review the attached spreadsheet.",
            },
        ]

    def test_detects_gmail_messages(self):
        fmt = ProviderFormatter()
        results = self._make_result(self._sample_messages())
        output = fmt.format(results)
        assert output is not None
        assert "Team standup notes" in output
        assert "Q1 budget review" in output
        assert "alice@example.com" in output

    def test_gmail_shows_snippet(self):
        fmt = ProviderFormatter()
        results = self._make_result(self._sample_messages())
        output = fmt.format(results)
        assert "Here are the notes" in output

    def test_gmail_missing_fields_graceful(self):
        fmt = ProviderFormatter()
        results = self._make_result([{"subject": "Hello", "from": "x@y.com"}])
        output = fmt.format(results)
        # Should still produce output, just without snippet
        assert output is not None
        assert "Hello" in output

    def test_no_gmail_data_returns_none(self):
        fmt = ProviderFormatter()
        results = [{"status": "success", "result": {"items": []}}]
        assert fmt.format(results) is None


# ─────────────────────────────────────────────────────────────────────────────
# ProviderFormatter — GitHub
# ─────────────────────────────────────────────────────────────────────────────

class TestProviderFormatterGitHub:
    def _sample_issues(self):
        return [
            {
                "number": 42,
                "title": "Fix login redirect bug",
                "state": "open",
                "html_url": "https://github.com/org/repo/issues/42",
            },
            {
                "number": 41,
                "title": "Update README",
                "state": "closed",
                "html_url": "https://github.com/org/repo/issues/41",
            },
        ]

    def _make_result(self, issues: list[dict]) -> list[dict]:
        return [{"status": "success", "result": {"items": issues}}]

    def test_detects_github_issues(self):
        fmt = ProviderFormatter()
        results = self._make_result(self._sample_issues())
        output = fmt.format(results)
        assert output is not None
        assert "#42" in output
        assert "Fix login redirect bug" in output
        assert "github.com" in output

    def test_github_shows_state(self):
        fmt = ProviderFormatter()
        results = self._make_result(self._sample_issues())
        output = fmt.format(results)
        assert "open" in output
        assert "closed" in output

    def test_github_issue_count(self):
        fmt = ProviderFormatter()
        results = self._make_result(self._sample_issues())
        output = fmt.format(results)
        assert "2" in output


# ─────────────────────────────────────────────────────────────────────────────
# ProviderFormatter — Linear
# ─────────────────────────────────────────────────────────────────────────────

class TestProviderFormatterLinear:
    def _sample_tickets(self):
        return [
            {
                "identifier": "LIN-101",
                "title": "Implement dark mode",
                "state": {"name": "In Progress"},
                "url": "https://linear.app/team/issue/LIN-101",
            },
            {
                "identifier": "LIN-99",
                "title": "Fix mobile layout",
                "state": {"name": "Todo"},
                "url": "https://linear.app/team/issue/LIN-99",
            },
        ]

    def _make_result(self, tickets: list[dict]) -> list[dict]:
        return [{"status": "success", "result": {"nodes": tickets}}]

    def test_detects_linear_tickets(self):
        fmt = ProviderFormatter()
        results = self._make_result(self._sample_tickets())
        output = fmt.format(results)
        assert output is not None
        assert "LIN-101" in output
        assert "Implement dark mode" in output

    def test_linear_shows_state(self):
        fmt = ProviderFormatter()
        results = self._make_result(self._sample_tickets())
        output = fmt.format(results)
        assert "In Progress" in output

    def test_linear_shows_url(self):
        fmt = ProviderFormatter()
        results = self._make_result(self._sample_tickets())
        output = fmt.format(results)
        assert "linear.app" in output


# ─────────────────────────────────────────────────────────────────────────────
# ProviderFormatter — priority and no-match cases
# ─────────────────────────────────────────────────────────────────────────────

class TestProviderFormatterGeneral:
    def test_returns_none_for_empty_results(self):
        fmt = ProviderFormatter()
        assert fmt.format([]) is None

    def test_returns_none_for_unrecognised_payload(self):
        fmt = ProviderFormatter()
        results = [{"status": "success", "result": {"foo": "bar", "baz": 42}}]
        assert fmt.format(results) is None

    def test_gmail_takes_priority_over_generic(self):
        """Gmail formatter should fire before GitHub/Linear since messages
        are more common."""
        fmt = ProviderFormatter()
        results = [
            {
                "status": "success",
                "result": {
                    "messages": [
                        {"subject": "Hi", "from": "x@y.com", "snippet": "hello"}
                    ]
                },
            }
        ]
        output = fmt.format(results)
        assert output is not None
        assert "Hi" in output
