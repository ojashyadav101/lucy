"""Phase 2: Connection & Claim Safety for Lucy.

Three components:

1. AuthResponseBuilder
   Generates actionable "connect X here → [link]" messages when a required
   integration is not yet connected. Prevents the LLM from hallucinating CLI
   instructions or generic error text when an auth failure occurs.

2. ClaimValidator
   Post-processes LLM response text and strips or qualifies completeness
   phrases ("that's all", "here are all your …", "nothing else") when the
   underlying tool payload was truncated or the result set is known to be
   partial. Prevents false "I showed you everything" assertions.

3. ProviderFormatter
   Deterministic per-provider response builders for Gmail, GitHub issues, and
   Linear tickets. When the LLM fails to produce final prose from tool results
   (no-text fallback), these formatters construct a grounded, templated response
   directly from the raw tool payload — no LLM summarisation involved.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import structlog

logger = structlog.get_logger()

# Imported here so tests can patch lucy.core.safety.get_composio_client
from lucy.integrations.composio_client import get_composio_client  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# App metadata registry
# ─────────────────────────────────────────────────────────────────────────────

# composio app slug → (human name, one-line capability description)
APP_METADATA: dict[str, tuple[str, str]] = {
    "googlecalendar": ("Google Calendar", "view and manage your calendar events"),
    "gmail": ("Gmail", "read and send email"),
    "googledrive": ("Google Drive", "access and manage your Drive files"),
    "googledocs": ("Google Docs", "read and edit documents"),
    "googlesheets": ("Google Sheets", "read and edit spreadsheets"),
    "github": ("GitHub", "manage repos, issues, and pull requests"),
    "linear": ("Linear", "view and update tickets and sprints"),
    "notion": ("Notion", "read and write Notion pages"),
    "slack": ("Slack", "send messages and read channels"),
    "jira": ("Jira", "view and update Jira issues"),
    "trello": ("Trello", "manage Trello boards and cards"),
    "figma": ("Figma", "view Figma designs"),
    "asana": ("Asana", "manage Asana tasks and projects"),
}


def _app_human_name(slug: str) -> str:
    return APP_METADATA.get(slug.lower(), (slug.title(), ""))[0]


# ─────────────────────────────────────────────────────────────────────────────
# 1. AuthResponseBuilder
# ─────────────────────────────────────────────────────────────────────────────

class AuthResponseBuilder:
    """Build actionable connection-required responses for unconnected integrations."""

    async def build(
        self,
        apps: list[str],
        workspace_id: UUID,
    ) -> str:
        """Return a Slack-formatted response prompting the user to connect apps.

        Attempts to generate a real Composio OAuth link per app. Falls back to
        a generic instruction if the link cannot be generated.
        """
        client = get_composio_client()

        lines: list[str] = []

        if len(apps) == 1:
            app = apps[0]
            name = _app_human_name(app)
            link = await self._get_link(client, str(workspace_id), app)
            lines.append(f"I need access to *{name}* to do that.")
            if link:
                lines.append(f":point_right: Connect it here: {link}")
                lines.append("Once connected, just ask me again and I'll pick up right where we left off.")
            else:
                lines.append(
                    f"Please connect *{name}* through the integrations settings and then ask me again."
                )
        else:
            names = [_app_human_name(a) for a in apps]
            lines.append(
                f"I need access to *{', '.join(names[:-1])}* and *{names[-1]}* to do that."
            )
            for app in apps:
                name = _app_human_name(app)
                link = await self._get_link(client, str(workspace_id), app)
                if link:
                    lines.append(f"• Connect *{name}*: {link}")
                else:
                    lines.append(f"• Connect *{name}* through integrations settings")
            lines.append("\nOnce connected, ask me again and I'll take it from there.")

        return "\n".join(lines)

    async def build_for_tool_error(
        self,
        tool_name: str,
        error_text: str,
        workspace_id: UUID,
    ) -> str:
        """Build a structured auth-error message for a specific tool failure.

        Infers the app slug from the tool name prefix (e.g. GOOGLECALENDAR_*).
        Returns a connect-link response rather than the raw SDK error.
        """
        app = self._infer_app_from_tool(tool_name)
        if not app:
            # Unknown app — surface a sanitised version of the error without CLI hints
            return (
                "I ran into an authorisation issue trying to complete that. "
                "Please check that the relevant integration is connected and try again."
            )

        client = get_composio_client()
        name = _app_human_name(app)
        link = await self._get_link(client, str(workspace_id), app)

        if link:
            return (
                f"I don\u2019t have access to *{name}* yet. "
                f"Connect it here and I\u2019ll be able to help:\n"
                f":point_right: {link}\n\n"
                f"Once connected, just ask me again."
            )
        return (
            f"I don\u2019t have access to *{name}* yet. "
            f"Please connect it through the integrations settings and ask me again."
        )

    @staticmethod
    def _infer_app_from_tool(tool_name: str) -> str | None:
        """Map a Composio tool slug to its app slug.

        Examples:
          GOOGLECALENDAR_EVENTS_LIST → googlecalendar
          GITHUB_LIST_ISSUES         → github
          GMAIL_SEND_EMAIL           → gmail
        """
        tool_upper = tool_name.upper()
        _prefix_map = {
            "GOOGLECALENDAR_": "googlecalendar",
            "GMAIL_": "gmail",
            "GOOGLEDRIVE_": "googledrive",
            "GOOGLEDOCS_": "googledocs",
            "GOOGLESHEETS_": "googlesheets",
            "GITHUB_": "github",
            "LINEAR_": "linear",
            "NOTION_": "notion",
            "SLACK_": "slack",
            "JIRA_": "jira",
            "TRELLO_": "trello",
            "FIGMA_": "figma",
            "ASANA_": "asana",
        }
        for prefix, slug in _prefix_map.items():
            if tool_upper.startswith(prefix):
                return slug
        return None

    @staticmethod
    async def _get_link(client: Any, workspace_id: str, app: str) -> str | None:
        try:
            link = await client.create_connection_link(
                entity_id=workspace_id,
                app=app,
            )
            return link
        except Exception as e:
            logger.warning("auth_link_generation_failed", app=app, error=str(e))
            return None


# ─────────────────────────────────────────────────────────────────────────────
# 2. ClaimValidator
# ─────────────────────────────────────────────────────────────────────────────

# Phrases that assert the data shown is complete / exhaustive
_COMPLETENESS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bthat'?s\s+all\b", re.IGNORECASE),
    re.compile(r"\bthose are all\b", re.IGNORECASE),
    re.compile(r"\bthat'?s\s+everything\b", re.IGNORECASE),
    re.compile(r"\bnothing\s+else\b", re.IGNORECASE),
    re.compile(r"\bno\s+more\b", re.IGNORECASE),
    re.compile(r"\bcomplete\s+list\b", re.IGNORECASE),
    re.compile(r"\bfull\s+list\b", re.IGNORECASE),
    re.compile(r"\ball\s+(?:of\s+)?your\s+\w+\b", re.IGNORECASE),
    re.compile(r"\bhere\s+are\s+all\b", re.IGNORECASE),
    re.compile(r"\bthat'?s\s+the\s+(?:complete|full|entire)\b", re.IGNORECASE),
    re.compile(r"\bI\s+(?:have\s+)?listed\s+all\b", re.IGNORECASE),
    re.compile(r"\bno\s+(?:other|additional)\s+\w+\s+(?:found|scheduled|exist)\b", re.IGNORECASE),
    re.compile(r"\bcovers?\s+everything\b", re.IGNORECASE),
    re.compile(r"\bthis\s+is\s+everything\b", re.IGNORECASE),
    re.compile(r"\beverything\s+(?:on|in|from)\s+your\b", re.IGNORECASE),
]

_PARTIAL_DISCLAIMER = " *(note: results may be partial — the data was too large to retrieve in full)*"


class ClaimValidator:
    """Strip or qualify false completeness claims from LLM responses.

    Usage:
        validator = ClaimValidator()
        safe_text = validator.validate(response_text, is_partial=True)
    """

    def validate(self, text: str, is_partial: bool) -> str:
        """Return the text with completeness claims qualified if data is partial.

        Args:
            text: The LLM-generated response text.
            is_partial: True if any tool result was truncated or the payload
                        was explicitly marked as incomplete.

        Returns:
            Original text if not partial, otherwise qualified text.
        """
        if not is_partial or not text.strip():
            return text

        found_any = any(p.search(text) for p in _COMPLETENESS_PATTERNS)
        if not found_any:
            return text

        # Replace the first completeness claim with a qualified version
        qualified = text
        for pattern in _COMPLETENESS_PATTERNS:
            if pattern.search(qualified):
                qualified = pattern.sub(
                    lambda m: m.group(0) + _PARTIAL_DISCLAIMER,
                    qualified,
                    count=1,
                )
                break  # one qualification is enough

        logger.info("claim_validator_qualified_response")
        return qualified

    @staticmethod
    def response_is_partial(tool_result_contents: list[str]) -> bool:
        """Check if any of the serialised tool result strings contain a truncation marker."""
        return any("[TRUNCATED:" in c for c in tool_result_contents)


# ─────────────────────────────────────────────────────────────────────────────
# 3. ProviderFormatter
# ─────────────────────────────────────────────────────────────────────────────

class ProviderFormatter:
    """Deterministic per-provider response builders.

    Tries each registered formatter against the raw tool results.
    Returns the first non-empty formatted string, or None if no formatter
    recognises the payload.
    """

    def format(
        self,
        tool_results: list[dict[str, Any]],
        intent_text: str = "",
    ) -> str | None:
        """Try all formatters; return the first match."""
        for fn in (
            self._format_gmail,
            self._format_github_issues,
            self._format_linear_tickets,
        ):
            result = fn(tool_results)
            if result:
                return result
        return None

    # ------------------------------------------------------------------
    # Gmail
    # ------------------------------------------------------------------

    def _format_gmail(self, tool_results: list[dict[str, Any]]) -> str | None:
        messages = self._extract_items_by_fields(
            tool_results,
            required_any={"subject", "from", "snippet"},
        )
        if not messages:
            return None

        lines = [f"Here are your {len(messages)} most recent email(s):"]
        for i, msg in enumerate(messages, 1):
            subject = msg.get("subject") or msg.get("Subject") or "(no subject)"
            sender = msg.get("from") or msg.get("From") or "unknown sender"
            date_raw = msg.get("date") or msg.get("Date") or msg.get("internalDate")
            date_str = self._fmt_email_date(date_raw)
            snippet = (msg.get("snippet") or "")[:120]
            line = f"{i}. *{subject}* — from {sender}"
            if date_str:
                line += f" ({date_str})"
            if snippet:
                line += f"\n   _{snippet}_"
            lines.append(line)

        return "\n".join(lines)

    @staticmethod
    def _fmt_email_date(raw: Any) -> str:
        if not raw:
            return ""
        # internalDate is epoch ms as string
        if isinstance(raw, (str, int)) and str(raw).isdigit() and len(str(raw)) == 13:
            try:
                dt = datetime.fromtimestamp(int(raw) / 1000, tz=timezone.utc)
                return dt.strftime("%b %d")
            except Exception:
                pass
        if isinstance(raw, str):
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                return dt.strftime("%b %d")
            except Exception:
                return raw[:16]
        return str(raw)[:16]

    # ------------------------------------------------------------------
    # GitHub Issues / PRs
    # ------------------------------------------------------------------

    def _format_github_issues(self, tool_results: list[dict[str, Any]]) -> str | None:
        items = self._extract_items_by_fields(
            tool_results,
            required_all={"number", "title"},
        )
        if not items:
            return None

        # Confirm these look like GitHub (state field or html_url with github.com)
        sample = items[0]
        url = sample.get("html_url") or sample.get("url") or ""
        if "github.com" not in url and "state" not in sample:
            return None

        kind = "pull request" if any(i.get("pull_request") for i in items) else "issue"
        lines = [f"Here are your {len(items)} GitHub {kind}(s):"]
        for item in items:
            num = item.get("number", "?")
            title = item.get("title") or "(no title)"
            state = item.get("state") or ""
            link = item.get("html_url") or item.get("url") or ""
            line = f"• #{num}: {title}"
            if state:
                line += f" [{state}]"
            if link:
                line += f" — {link}"
            lines.append(line)

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Linear Tickets
    # ------------------------------------------------------------------

    def _format_linear_tickets(self, tool_results: list[dict[str, Any]]) -> str | None:
        items = self._extract_items_by_fields(
            tool_results,
            required_all={"identifier", "title"},
        )
        if not items:
            return None

        lines = [f"Here are your {len(items)} Linear ticket(s):"]
        for item in items:
            identifier = item.get("identifier", "?")
            title = item.get("title") or "(no title)"
            state = ""
            state_obj = item.get("state")
            if isinstance(state_obj, dict):
                state = state_obj.get("name") or ""
            elif isinstance(state_obj, str):
                state = state_obj
            url = item.get("url") or ""
            line = f"• {identifier}: {title}"
            if state:
                line += f" [{state}]"
            if url:
                line += f" — {url}"
            lines.append(line)

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_items_by_fields(
        tool_results: list[dict[str, Any]],
        required_all: set[str] | None = None,
        required_any: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Recursively find list items from tool results that match field criteria."""
        found: list[dict[str, Any]] = []

        def _walk(obj: Any) -> None:
            if isinstance(obj, dict):
                # Check if this dict itself matches
                keys = set(k.lower() for k in obj)
                all_match = (not required_all) or required_all.issubset(keys)
                any_match = (not required_any) or bool(required_any & keys)
                if all_match and any_match:
                    found.append(obj)
                else:
                    for v in obj.values():
                        _walk(v)
            elif isinstance(obj, list):
                # Only recurse into lists that contain dicts matching the criteria
                candidates = [i for i in obj if isinstance(i, dict)]
                if candidates:
                    sample_keys = set(k.lower() for k in candidates[0])
                    all_match = (not required_all) or required_all.issubset(sample_keys)
                    any_match = (not required_any) or bool(required_any & sample_keys)
                    if all_match and any_match:
                        found.extend(candidates)
                    else:
                        for item in obj:
                            _walk(item)

        for tr in tool_results:
            _walk(tr.get("result") or tr)

        return found


# ─────────────────────────────────────────────────────────────────────────────
# Singletons
# ─────────────────────────────────────────────────────────────────────────────

_auth_builder = AuthResponseBuilder()
_claim_validator = ClaimValidator()
_provider_formatter = ProviderFormatter()


def get_auth_builder() -> AuthResponseBuilder:
    return _auth_builder


def get_claim_validator() -> ClaimValidator:
    return _claim_validator


def get_provider_formatter() -> ProviderFormatter:
    return _provider_formatter
