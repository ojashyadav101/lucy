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


async def get_local_messages(
    ws: object,  # WorkspaceFS
    since_iso: str,
    channel_names: list[str] | None = None,
) -> str:
    """Read synchronized Slack messages from local filesystem.

    Reads files in slack_logs/{channel_name}/{YYYY-MM-DD}.md
    Filters out messages before since_iso timestamp.
    
    Args:
        ws: The WorkspaceFS instance.
        since_iso: ISO 8601 timestamp string (e.g. "2026-02-24T10:00:00").
        channel_names: Optional list of specific channels to read.
                       If None, reads all available channels.

    Returns:
        A combined Markdown string of new messages grouped by channel.
    """
    from datetime import datetime, timezone
    
    try:
        since_dt = datetime.fromisoformat(since_iso.replace("Z", "+00:00"))
    except ValueError:
        return "(Invalid since_iso timestamp)"
        
    since_date = since_dt.strftime("%Y-%m-%d")
    
    # List channels in slack_logs/
    channels_to_read = channel_names
    if not channels_to_read:
        logs_dir = ws.root / "slack_logs"
        if logs_dir.is_dir():
            channels_to_read = [d.name for d in logs_dir.iterdir() if d.is_dir()]
        else:
            channels_to_read = []
            
    if not channels_to_read:
        return "(No local Slack logs found)"
        
    output_parts: list[str] = []
    
    for channel in channels_to_read:
        chan_dir = ws.root / "slack_logs" / channel
        if not chan_dir.is_dir():
            continue
            
        # Get all daily logs that are >= since_date
        daily_logs = [
            f.name for f in chan_dir.iterdir() 
            if f.is_file() and f.name.endswith(".md") and f.name[:-3] >= since_date
        ]
        daily_logs.sort()
        
        channel_msgs = []
        for log_file in daily_logs:
            date_str = log_file[:-3]
            try:
                content = (chan_dir / log_file).read_text("utf-8")
                lines = content.splitlines()
                for line in lines:
                    # Line format: [HH:MM:SS] <user> text
                    if line.startswith("[") and "]" in line:
                        time_str = line[1:line.find("]")]
                        msg_dt_str = f"{date_str}T{time_str}Z"
                        try:
                            msg_dt = datetime.fromisoformat(msg_dt_str.replace("Z", "+00:00"))
                            if msg_dt > since_dt:
                                channel_msgs.append(line)
                        except ValueError:
                            # Parse error, include just in case if date is > since_date
                            if date_str > since_date:
                                channel_msgs.append(line)
            except Exception as e:
                logger.debug("failed_reading_log", path=str(chan_dir / log_file), error=str(e))
                
        if channel_msgs:
            output_parts.append(f"### #{channel}\n" + "\n".join(channel_msgs) + "\n")
            
    if not output_parts:
        return "(No new messages since last check)"
        
    return "\n".join(output_parts)


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
