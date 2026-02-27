"""Slack channel reader for crons and monitoring."""

from __future__ import annotations

import structlog

logger = structlog.get_logger()


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
