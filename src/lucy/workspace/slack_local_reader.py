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
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import structlog

from lucy.workspace.filesystem import WorkspaceFS

logger = structlog.get_logger()

_LOG_LINE_RE = re.compile(r"^\[(\d{2}:\d{2}:\d{2})\]\s+<([^>]+)>\s+(.+)$")
_QUESTION_RE = re.compile(r"\?$|^(what|how|when|where|why|who|can|could|should|is|are|do|does|did|will|would)\b", re.IGNORECASE)
_MAX_CHARS_PER_CHANNEL = 3000
_MAX_TOTAL_CHARS = 12000


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


async def get_new_slack_messages(
    ws: WorkspaceFS,
    since: datetime | None = None,
    channel_names: list[str] | None = None,
    include_threads: bool = True,
    max_messages: int = 200,
) -> str:
    """Read local synced Slack files and return new messages since last check.

    Groups by channel with thread context. No API calls -- reads filesystem only.

    Args:
        ws: Workspace filesystem.
        since: Only return messages after this time. None = last 4 hours.
        channel_names: Optional list of channels to scan. None = all channels.
        include_threads: Whether to include thread replies.
        max_messages: Maximum messages to return across all channels.

    Returns:
        Formatted text grouped by channel, ready for LLM consumption.
    """
    logs_dir = ws.root / "slack_logs"
    if not logs_dir.is_dir():
        return "No Slack history available yet. Messages are synced every 10 minutes."

    if since is None:
        since = datetime.now(timezone.utc) - timedelta(hours=4)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    since_time_str = since.strftime("%H:%M:%S")
    since_date_str = since.strftime("%Y-%m-%d")

    # Determine which channels to read
    if channel_names:
        channel_dirs = [logs_dir / ch for ch in channel_names if (logs_dir / ch).is_dir()]
    else:
        channel_dirs = sorted(
            [d for d in logs_dir.iterdir() if d.is_dir() and not d.name.startswith("_")]
        )

    sections: list[str] = []
    total_messages = 0

    for ch_dir in channel_dirs:
        channel_name = ch_dir.name
        channel_msgs: list[tuple[str, str, str, str]] = []  # (date, time, user, text)

        # Read today and yesterday log files (those are the "recent" ones)
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
                # Filter by time if same date as since
                if date_str == since_date_str and time_str <= since_time_str:
                    continue
                channel_msgs.append((date_str, time_str, user, text))

        if not channel_msgs:
            continue

        # Build channel section
        lines = [f"### #{channel_name} ({len(channel_msgs)} new messages)"]
        char_count = 0
        for date_str, time_str, user, text in channel_msgs[-50:]:  # last 50 per channel
            entry = f"  [{date_str} {time_str}] <{user}> {text}"
            if char_count + len(entry) > _MAX_CHARS_PER_CHANNEL:
                lines.append("  ... (truncated)")
                break
            lines.append(entry)
            char_count += len(entry)
            total_messages += 1

        # Include recent thread replies if requested
        if include_threads:
            threads_dir = ch_dir / "threads"
            if threads_dir.is_dir():
                thread_snippets: list[str] = []
                for thread_file in sorted(threads_dir.glob("*.md"), reverse=True)[:5]:
                    try:
                        ts_float = float(thread_file.stem)
                        thread_dt = datetime.fromtimestamp(ts_float, tz=timezone.utc)
                        if thread_dt < since:
                            continue
                        content = thread_file.read_text(encoding="utf-8")
                        thread_lines = [l for l in content.splitlines() if l.strip()]
                        if thread_lines:
                            thread_snippets.append(
                                f"  [thread {thread_file.stem}]:"
                                + " | ".join(
                                    (parsed[2][:60] if (parsed := _parse_log_line(l)) else l[:60])
                                    for l in thread_lines[-3:]
                                )
                            )
                    except (ValueError, OSError, UnicodeDecodeError):
                        continue
                if thread_snippets:
                    lines.append("  Thread activity:")
                    lines.extend(thread_snippets)

        sections.append("\n".join(lines))

        if total_messages >= max_messages:
            break

    if not sections:
        since_str = since.strftime("%Y-%m-%d %H:%M")
        return f"No new messages since {since_str} UTC."

    header = f"New Slack messages since {since.strftime('%Y-%m-%d %H:%M')} UTC:\n"
    result = header + "\n\n".join(sections)

    # Final safety truncation
    if len(result) > _MAX_TOTAL_CHARS:
        result = result[:_MAX_TOTAL_CHARS] + "\n...(truncated for length)"

    return result


async def get_last_heartbeat_time(ws: WorkspaceFS) -> datetime | None:
    """Parse execution.log to find the timestamp of the last successful heartbeat.

    Returns None if no successful run has been logged yet.
    """
    log_content = await ws.read_file("crons/heartbeat/execution.log")
    if not log_content:
        return None

    # Log format: ## 2026-02-28T18:30:00+00:00 (elapsed: 12340ms, status: delivered)
    # or:         ## 2026-02-28T18:30:00+00:00 (elapsed: 1240ms, status: skipped)
    ts_pattern = re.compile(
        r"^## (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+\-]\d{2}:\d{2})",
        re.MULTILINE,
    )
    matches = ts_pattern.findall(log_content)

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

    since = datetime.now(timezone.utc) - timedelta(hours=hours_back)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
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


async def get_unanswered_questions(
    ws: WorkspaceFS,
    hours_back: int = 8,
    bot_user_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Find messages that look like questions with no subsequent reply.

    Heuristic: a question-looking message is "unanswered" if no other
    message appeared in the same channel (or thread) within 2 hours.

    Args:
        ws: Workspace filesystem.
        hours_back: How many hours of history to scan.
        bot_user_ids: User IDs to treat as bots (skip their messages).

    Returns:
        List of dicts with keys: channel, time, user, text, age_hours.
    """
    logs_dir = ws.root / "slack_logs"
    if not logs_dir.is_dir():
        return []

    bot_ids = set(bot_user_ids or [])
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=hours_back)
    today = now.strftime("%Y-%m-%d")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    candidates: list[dict[str, Any]] = []

    channel_dirs = [
        d for d in logs_dir.iterdir() if d.is_dir() and not d.name.startswith("_")
    ]

    for ch_dir in channel_dirs:
        channel_name = ch_dir.name
        # Collect all messages for this channel in the window, in order
        all_msgs: list[tuple[str, str, str, str]] = []  # (date, time, user, text)

        for date_str in [yesterday, today]:
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
                msg_dt = datetime.strptime(
                    f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S"
                ).replace(tzinfo=timezone.utc)
                if msg_dt < since:
                    continue
                all_msgs.append((date_str, time_str, user, text))

        all_msgs.sort(key=lambda x: (x[0], x[1]))

        for i, (date_str, time_str, user, text) in enumerate(all_msgs):
            if user in bot_ids:
                continue
            if not _is_question(text):
                continue

            msg_dt = datetime.strptime(
                f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=timezone.utc)

            # Check if any message followed this one within 2 hours
            answered = False
            for j in range(i + 1, len(all_msgs)):
                next_date, next_time, _, _ = all_msgs[j]
                next_dt = datetime.strptime(
                    f"{next_date} {next_time}", "%Y-%m-%d %H:%M:%S"
                ).replace(tzinfo=timezone.utc)
                if (next_dt - msg_dt).total_seconds() > 7200:
                    break
                answered = True
                break

            if not answered and msg_dt < (now - timedelta(hours=2)):
                age_hours = round((now - msg_dt).total_seconds() / 3600, 1)
                candidates.append({
                    "channel": channel_name,
                    "date": date_str,
                    "time": time_str,
                    "user": user,
                    "text": text,
                    "age_hours": age_hours,
                })

    # Sort by age descending (oldest unanswered first)
    candidates.sort(key=lambda x: x["age_hours"], reverse=True)
    return candidates[:20]
