"""Slack message reader for crons and monitoring.

Fetches recent messages from Slack channels so that crons
(heartbeat, issue monitor) can review what's been happening.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import structlog

logger = structlog.get_logger()


@dataclass
class SlackMessage:
    """A Slack message with metadata."""

    channel_id: str
    channel_name: str
    user_id: str
    user_name: str
    text: str
    timestamp: str
    thread_ts: str | None = None
    reply_count: int = 0


async def get_new_messages(
    slack_client: object,
    channels: list[str],
    since_ts: str | None = None,
    limit_per_channel: int = 50,
) -> list[SlackMessage]:
    """Fetch new Slack messages across channels since a timestamp.

    Args:
        slack_client: Slack Bolt async client.
        channels: List of channel IDs to read from.
        since_ts: Only return messages after this Slack timestamp.
        limit_per_channel: Max messages to fetch per channel.

    Returns:
        List of SlackMessage objects, newest first.
    """
    messages: list[SlackMessage] = []

    for channel_id in channels:
        try:
            kwargs: dict = {
                "channel": channel_id,
                "limit": limit_per_channel,
            }
            if since_ts:
                kwargs["oldest"] = since_ts

            result = await slack_client.conversations_history(**kwargs)  # type: ignore[attr-defined]

            channel_info = await slack_client.conversations_info(  # type: ignore[attr-defined]
                channel=channel_id
            )
            channel_name = channel_info.get("channel", {}).get("name", channel_id)

            for msg in result.get("messages", []):
                if msg.get("subtype"):
                    continue

                user_id = msg.get("user", "")
                messages.append(SlackMessage(
                    channel_id=channel_id,
                    channel_name=channel_name,
                    user_id=user_id,
                    user_name=user_id,
                    text=msg.get("text", ""),
                    timestamp=msg.get("ts", ""),
                    thread_ts=msg.get("thread_ts"),
                    reply_count=msg.get("reply_count", 0),
                ))

        except Exception as e:
            logger.warning(
                "slack_read_failed",
                channel_id=channel_id,
                error=str(e),
            )

    messages.sort(key=lambda m: m.timestamp, reverse=True)
    return messages


async def get_lucy_channels(slack_client: object) -> list[dict[str, str]]:
    """List all channels Lucy is a member of.

    Returns list of {id, name, purpose} dicts.
    """
    try:
        result = await slack_client.conversations_list(  # type: ignore[attr-defined]
            types="public_channel,private_channel",
            exclude_archived=True,
        )
        channels = []
        for ch in result.get("channels", []):
            if ch.get("is_member"):
                channels.append({
                    "id": ch["id"],
                    "name": ch.get("name", ""),
                    "purpose": ch.get("purpose", {}).get("value", ""),
                })
        return channels

    except Exception as e:
        logger.warning("slack_list_channels_failed", error=str(e))
        return []
