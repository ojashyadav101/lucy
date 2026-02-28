"""Slack history search — internal tool for searching synced messages.

Viktor's biggest advantage is instant grep access to all Slack history.
The sync cron (slack_sync.py) already writes messages to:
    workspaces/{id}/slack_logs/{channel_name}/{YYYY-MM-DD}.md

This module provides the search function that the agent can call
as an internal tool — no Composio, no external API.

Usage in agent loop:
    results = await search_slack_history(ws, "pricing discussion")
    # Returns formatted context the agent can reason about.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import structlog

from lucy.workspace.filesystem import WorkspaceFS

logger = structlog.get_logger()

MAX_RESULTS = 30
MAX_CONTEXT_LINES = 3  # Lines of context around each match
SNIPPET_MAX_CHARS = 200


@dataclass
class SearchResult:
    """A single search hit from Slack history."""

    channel: str
    date: str
    time: str
    user: str
    text: str
    line_number: int

    @property
    def display(self) -> str:
        return f"[{self.date} {self.time}] #{self.channel} <{self.user}> {self.text}"


async def search_slack_history(
    ws: WorkspaceFS,
    query: str,
    *,
    channel: str | None = None,
    days_back: int = 30,
    max_results: int = MAX_RESULTS,
) -> list[SearchResult]:
    """Search synced Slack history for a query string.

    Args:
        ws: Workspace filesystem.
        query: Search term (case-insensitive substring match).
        channel: Optional channel name filter (e.g. "general").
        days_back: How many days of history to search (default 30).
        max_results: Maximum results to return.

    Returns:
        List of SearchResult objects, newest first.
    """
    logs_dir = ws.root / "slack_logs"
    if not logs_dir.is_dir():
        logger.debug("no_slack_logs_dir", workspace_id=ws.workspace_id)
        return []

    # Determine which channels to search
    if channel:
        channel_dirs = [logs_dir / channel]
    else:
        channel_dirs = [
            d for d in sorted(logs_dir.iterdir())
            if d.is_dir() and not d.name.startswith("_")
        ]

    # Determine date range
    now = datetime.now(timezone.utc)
    earliest = now - timedelta(days=days_back)
    earliest_str = earliest.strftime("%Y-%m-%d")

    pattern = re.compile(re.escape(query), re.IGNORECASE)
    results: list[SearchResult] = []

    for ch_dir in channel_dirs:
        if not ch_dir.is_dir():
            continue
        channel_name = ch_dir.name

        # Iterate log files in reverse chronological order
        log_files = sorted(ch_dir.glob("*.md"), reverse=True)
        for log_file in log_files:
            date_str = log_file.stem  # "2026-02-23"
            if date_str < earliest_str:
                break

            try:
                content = log_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            for i, line in enumerate(content.splitlines()):
                if not pattern.search(line):
                    continue

                # Parse line format: [HH:MM:SS] <USER_ID> text
                parsed = _parse_log_line(line)
                if not parsed:
                    continue

                time_str, user, text = parsed
                results.append(SearchResult(
                    channel=channel_name,
                    date=date_str,
                    time=time_str,
                    user=user,
                    text=text,
                    line_number=i + 1,
                ))

                if len(results) >= max_results:
                    break

            if len(results) >= max_results:
                break

        if len(results) >= max_results:
            break

    logger.info(
        "slack_history_search",
        workspace_id=ws.workspace_id,
        query=query[:50],
        channel=channel,
        days_back=days_back,
        results=len(results),
    )
    return results


async def get_channel_history(
    ws: WorkspaceFS,
    channel: str,
    date: str | None = None,
    limit: int = 50,
) -> str:
    """Get recent messages from a specific channel.

    Args:
        ws: Workspace filesystem.
        channel: Channel name (e.g. "general").
        date: Specific date (YYYY-MM-DD). None = today.
        limit: Max lines to return.

    Returns:
        Formatted message history string.
    """
    logs_dir = ws.root / "slack_logs" / channel
    if not logs_dir.is_dir():
        return f"No history found for #{channel}."

    if date:
        files = [logs_dir / f"{date}.md"]
    else:
        files = sorted(logs_dir.glob("*.md"), reverse=True)[:3]

    lines: list[str] = []
    for f in files:
        if not f.is_file():
            continue
        try:
            content = f.read_text(encoding="utf-8")
            date_str = f.stem
            for line in content.splitlines():
                if line.strip():
                    lines.append(f"[{date_str}] {line}")
        except (OSError, UnicodeDecodeError):
            continue

    if not lines:
        return f"No messages found in #{channel}."

    # Return newest first, limited
    return "\n".join(lines[-limit:])


async def list_available_channels(ws: WorkspaceFS) -> list[str]:
    """List channels that have synced history."""
    logs_dir = ws.root / "slack_logs"
    if not logs_dir.is_dir():
        return []
    return sorted(
        d.name for d in logs_dir.iterdir()
        if d.is_dir() and not d.name.startswith("_")
    )


def format_search_results(results: list[SearchResult]) -> str:
    """Format search results for injection into agent context.

    Groups by channel, shows date/time/user/text.
    """
    if not results:
        return "No matching messages found in Slack history."

    # Group by channel
    by_channel: dict[str, list[SearchResult]] = {}
    for r in results:
        by_channel.setdefault(r.channel, []).append(r)

    sections: list[str] = []
    for channel, hits in by_channel.items():
        lines = [f"### #{channel} ({len(hits)} matches)"]
        for hit in hits:
            text = hit.text[:SNIPPET_MAX_CHARS]
            if len(hit.text) > SNIPPET_MAX_CHARS:
                text += "..."
            lines.append(f"  [{hit.date} {hit.time}] {hit.user}: {text}")
        sections.append("\n".join(lines))

    header = f"Found {len(results)} messages across {len(by_channel)} channel(s):\n"
    return header + "\n\n".join(sections)


# ── Internal Tools Definition ───────────────────────────────────────────

def get_history_tool_definitions() -> list[dict[str, Any]]:
    """Return OpenAI-format tool definitions for the Slack history tools.

    These are injected alongside Composio tools so the agent can search
    history without any external API call.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": "lucy_search_slack_history",
                "description": (
                    "Search past Slack messages across all channels. "
                    "Use this when someone asks about past conversations, "
                    "what was discussed, or to find context from earlier messages. "
                    "Returns matching messages with channel, date, user, and text."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search term or phrase to find in message history.",
                        },
                        "channel": {
                            "type": "string",
                            "description": "Optional: limit search to a specific channel name (e.g. 'general').",
                        },
                        "days_back": {
                            "type": "integer",
                            "description": "How many days of history to search. Default: 30.",
                            "default": 30,
                        },
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "lucy_get_channel_history",
                "description": (
                    "Get recent messages from a specific Slack channel. "
                    "Use this to review what's been happening in a channel "
                    "or to get context for a conversation."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "channel": {
                            "type": "string",
                            "description": "Channel name (e.g. 'general', 'engineering').",
                        },
                        "date": {
                            "type": "string",
                            "description": "Optional: specific date in YYYY-MM-DD format.",
                        },
                    },
                    "required": ["channel"],
                },
            },
        },
    ]


async def execute_history_tool(
    ws: WorkspaceFS,
    tool_name: str,
    parameters: dict[str, Any],
) -> str:
    """Execute an internal history tool call.

    Returns the formatted string result.
    """
    if tool_name == "lucy_search_slack_history":
        results = await search_slack_history(
            ws,
            query=parameters.get("query", ""),
            channel=parameters.get("channel"),
            days_back=parameters.get("days_back", 30),
        )
        return format_search_results(results)

    elif tool_name == "lucy_get_channel_history":
        return await get_channel_history(
            ws,
            channel=parameters.get("channel", ""),
            date=parameters.get("date"),
        )

    return f"Unknown history tool: {tool_name}"


# ── Helpers ─────────────────────────────────────────────────────────────

_LOG_LINE_RE = re.compile(
    r"^\[(\d{2}:\d{2}:\d{2})\]\s+<([^>]+)>\s+(.+)$"
)


def _parse_log_line(line: str) -> tuple[str, str, str] | None:
    """Parse a log line into (time, user, text)."""
    m = _LOG_LINE_RE.match(line.strip())
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3)
