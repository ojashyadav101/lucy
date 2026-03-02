"""Slack channel reader for crons and monitoring."""

from __future__ import annotations

import structlog

logger = structlog.get_logger()


async def get_lucy_channels(slack_client: object) -> list[dict[str, str]]:
    """List all channels Lucy is a member of.

    Paginates through all results — the Slack API returns at most 200 channels
    per page and silently drops the rest if next_cursor is not followed.

    Returns list of {id, name, purpose} dicts.
    """
    try:
        channels = []
        cursor: str | None = None

        while True:
            kwargs: dict[str, object] = {
                "types": "public_channel,private_channel",
                "exclude_archived": True,
                "limit": 200,
            }
            if cursor:
                kwargs["cursor"] = cursor

            result = await slack_client.conversations_list(**kwargs)  # type: ignore[attr-defined]

            for ch in result.get("channels", []):
                if ch.get("is_member"):
                    channels.append({
                        "id": ch["id"],
                        "name": ch.get("name", ""),
                        "purpose": ch.get("purpose", {}).get("value", ""),
                    })

            # Follow next_cursor for pagination
            cursor = result.get("response_metadata", {}).get("next_cursor", "")
            if not cursor:
                break

        return channels

    except Exception as e:
        logger.warning("slack_list_channels_failed", error=str(e))
        return []
