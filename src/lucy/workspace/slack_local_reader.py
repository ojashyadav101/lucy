"""Slack local reader — fast file-based access to synced Slack history.

Viktor's proactiveness relies on instant access to all Slack messages via
local files. The slack_sync.py cron writes those files every 10 minutes.
This module reads them efficiently for the heartbeat and other crons.

No API calls — reads filesystem only. Instant.

File format written by slack_sync.py:
    workspaces/{id}/slack_logs/{channel}/{YYYY-MM-DD}.md
    workspaces/{id}/slack_logs/{channel}/threads/{thread_ts}.md

Each line: [HH:MM:SS] <USER_ID> message text
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import structlog

from lucy.workspace.filesystem import WorkspaceFS

logger = structlog.get_logger()

_LOG_LINE_RE = re.compile(r"^\[(\d{2}:\d{2}:\d{2})\]\s+<([^>]+)>\s+(.+)$")
_QUESTION_RE = re.compile(r"\?$|^(what|how|when|where|why|who|can|could|should|is|are|do|does|did|will|would)\b", re.IGNORECASE)  # noqa: E501


def _parse_log_line(line: str) -> tuple[str, str, str] | None:
    """Parse '[HH:MM:SS] <USER> text' into (time, user, text)."""
    m = _LOG_LINE_RE.match(line.strip())
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3)


def _is_question(text: str) -> bool:
    """Heuristic: does this message look like a question?"""
    text = text.strip()
    if text.endswith("?"):
        return True
    words = text.lower().split()
    if words and _QUESTION_RE.match(words[0]):
        return True
    return False


async def get_last_heartbeat_time(ws: WorkspaceFS) -> datetime | None:
    """Parse execution.log to find the timestamp of the last successful heartbeat.

    Returns None if no successful run has been logged yet.
    """
    log_content = await ws.read_file("crons/heartbeat/execution.log")
    if not log_content:
        return None

    # Log format: ## 2026-02-28T18:30:00+00:00 (elapsed: 12340ms, status: delivered)
    # or:         ## 2026-02-28T18:30:00+00:00 (elapsed: 1240ms, status: skipped)
    # Walk backwards to find the most recent non-FAILED run
    for line in reversed(log_content.splitlines()):
        m = re.match(r"^## (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+\-\d:]+)", line)
        if m and "FAILED" not in line:
            try:
                return datetime.fromisoformat(m.group(1))
            except ValueError:
                continue

    return None


async def get_channel_summary(
    ws: WorkspaceFS,
    hours_back: int = 4,
) -> str:
    """Return a compact per-channel activity summary for heartbeat consumption.

    Shows: channel name, message count, active users, any unanswered questions.
    Designed to be cheap (no API calls) and concise (<500 tokens).

    Args:
        ws: Workspace filesystem.
        hours_back: How many hours of history to summarize.

    Returns:
        Formatted summary string.
    """
    logs_dir = ws.root / "slack_logs"
    if not logs_dir.is_dir():
        return "No Slack history available."

    since = datetime.now(UTC) - timedelta(hours=hours_back)
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    yesterday = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%d")
    since_time_str = since.strftime("%H:%M:%S")
    since_date_str = since.strftime("%Y-%m-%d")

    channel_dirs = sorted(
        [d for d in logs_dir.iterdir() if d.is_dir() and not d.name.startswith("_")]
    )

    rows: list[str] = []
    total_new = 0

    for ch_dir in channel_dirs:
        channel_name = ch_dir.name
        msgs: list[tuple[str, str, str]] = []  # (time, user, text)

        for date_str in [today, yesterday]:
            if date_str < since_date_str:
                continue
            log_file = ch_dir / f"{date_str}.md"
            if not log_file.is_file():
                continue
            try:
                content = log_file.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            for line in content.splitlines():
                parsed = _parse_log_line(line)
                if not parsed:
                    continue
                time_str, user, text = parsed
                if date_str == since_date_str and time_str <= since_time_str:
                    continue
                msgs.append((time_str, user, text))

        if not msgs:
            continue

        users = sorted({u for _, u, _ in msgs})
        questions = [(t, u, txt) for t, u, txt in msgs if _is_question(txt)]

        row_parts = [f"#{channel_name}: {len(msgs)} msgs, {len(users)} user(s)"]
        if questions:
            # Show last unanswered question with context
            last_q = questions[-1]
            row_parts.append(f"| Unanswered Q at {last_q[0]}: \"{last_q[2][:80]}\"")
        rows.append("  ".join(row_parts))
        total_new += len(msgs)

    if not rows:
        return f"No new messages in the last {hours_back}h."

    header = f"Channel activity (last {hours_back}h, {total_new} total messages):\n"
    return header + "\n".join(rows)
