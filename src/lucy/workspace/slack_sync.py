"""Slack message sync — background cron that syncs channel messages
to the workspace filesystem for instant grep access.

Viktor's biggest advantage is instant grep access to all historical
messages. This cron bridges the gap by periodically syncing recent
messages from all channels Lucy is in to workspace logs.

File structure:
    workspaces/{id}/slack_logs/{channel_name}/{YYYY-MM-DD}.md
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog

from lucy.workspace.filesystem import WorkspaceFS

logger = structlog.get_logger()

SYNC_LIMIT_PER_CHANNEL = 100


async def sync_channel_messages(
    ws: WorkspaceFS,
    slack_client: Any,
    since_ts: str | None = None,
) -> int:
    """Sync recent messages from all channels Lucy is in to the filesystem.

    Returns total number of messages synced.
    """
    from lucy.workspace.slack_reader import get_lucy_channels

    channels = await get_lucy_channels(slack_client)
    if not channels:
        logger.debug("slack_sync_no_channels", workspace_id=ws.workspace_id)
        return 0

    total_synced = 0

    for ch in channels:
        channel_id = ch["id"]
        channel_name = ch.get("name", channel_id)

        try:
            kwargs: dict[str, Any] = {
                "channel": channel_id,
                "limit": SYNC_LIMIT_PER_CHANNEL,
            }
            if since_ts:
                kwargs["oldest"] = since_ts

            result = await slack_client.conversations_history(**kwargs)
            messages = result.get("messages", [])

            if not messages:
                continue

            by_date: dict[str, list[str]] = {}
            for msg in messages:
                if msg.get("subtype"):
                    continue

                ts = msg.get("ts", "")
                text = msg.get("text", "").strip()
                user = msg.get("user", "unknown")

                if not text:
                    continue

                dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
                date_key = dt.strftime("%Y-%m-%d")
                time_str = dt.strftime("%H:%M:%S")

                line = f"[{time_str}] <{user}> {text}"

                if date_key not in by_date:
                    by_date[date_key] = []
                by_date[date_key].append(line)

            for date_key, lines in by_date.items():
                lines.reverse()
                file_path = f"slack_logs/{channel_name}/{date_key}.md"
                content = "\n".join(lines) + "\n"

                existing = await ws.read_file(file_path)
                if existing:
                    existing_lines = set(existing.strip().split("\n"))
                    new_lines = [l for l in lines if l not in existing_lines]
                    if new_lines:
                        await ws.append_file(file_path, "\n".join(new_lines) + "\n")
                        total_synced += len(new_lines)
                else:
                    await ws.write_file(file_path, content)
                    total_synced += len(lines)

        except Exception as e:
            logger.warning(
                "slack_sync_channel_failed",
                channel=channel_name,
                error=str(e),
            )

    if total_synced > 0:
        logger.info(
            "slack_sync_complete",
            workspace_id=ws.workspace_id,
            channels=len(channels),
            messages_synced=total_synced,
        )

    return total_synced


async def get_last_sync_ts(ws: WorkspaceFS) -> str | None:
    """Read the last sync timestamp from state."""
    content = await ws.read_file("slack_logs/_last_sync_ts")
    if content:
        return content.strip()
    return None


async def save_last_sync_ts(ws: WorkspaceFS, ts: str) -> None:
    """Save the current sync timestamp."""
    await ws.write_file("slack_logs/_last_sync_ts", ts)
