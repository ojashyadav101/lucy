"""Proactive Slack tools — emoji reactions, channel posts, DMs.

These tools let the heartbeat and other cron agents take visible actions
in Slack: reacting to messages, posting to channels, sending DMs.

Viktor's most common proactive action is emoji reactions (30+ per session).
These are low-noise high-signal -- the team sees Lucy is paying attention
without being interrupted by a message.
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger()

_VALID_TOOLS = {
    "lucy_react_to_message",
    "lucy_post_to_channel",
    "lucy_send_dm",
}

_COMMON_EMOJIS = (
    "tada, eyes, rocket, fire, 100, thinking_face, white_check_mark, "
    "hugging_face, bulb, raised_hands, wave, heart, slightly_smiling_face, "
    "muscle, thumbsup, clap, star, sparkles, mega"
)


def get_slack_proactive_tool_definitions() -> list[dict[str, Any]]:
    """Return OpenAI-format tool definitions for proactive Slack actions."""
    return [
        {
            "type": "function",
            "function": {
                "name": "lucy_react_to_message",
                "description": (
                    "Add an emoji reaction to a Slack message. "
                    "Use this to acknowledge messages, celebrate team wins, "
                    "or signal awareness without interrupting with a reply. "
                    "This is your lowest-noise, highest-signal action. "
                    f"Common emojis: {_COMMON_EMOJIS}. "
                    "IMPORTANT: Only use standard Slack emoji names (no colons). "
                    "Do NOT react to your own messages."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "channel_id": {
                            "type": "string",
                            "description": "Slack channel ID (e.g. C0AGNRMGALS).",
                        },
                        "message_ts": {
                            "type": "string",
                            "description": "Timestamp of the message to react to (e.g. '1772284913.185259').",  # noqa: E501
                        },
                        "emoji": {
                            "type": "string",
                            "description": "Emoji name without colons (e.g. 'tada', 'eyes', 'rocket').",  # noqa: E501
                        },
                    },
                    "required": ["channel_id", "message_ts", "emoji"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "lucy_post_to_channel",
                "description": (
                    "Post a message to a specific Slack channel or thread. "
                    "Use this when you have something genuinely useful to share "
                    "proactively -- an insight, answer to an unanswered question, "
                    "or relevant update. "
                    "Prefer emoji reactions for simple acknowledgments. "
                    "Only post when you have real value to add."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "channel_id": {
                            "type": "string",
                            "description": "Slack channel ID (e.g. C0AGNRMGALS).",
                        },
                        "text": {
                            "type": "string",
                            "description": "Message text. Be concise and natural, as if reaching out to a teammate.",  # noqa: E501
                        },
                        "thread_ts": {
                            "type": "string",
                            "description": "Optional: reply in a thread by providing the parent message timestamp.",  # noqa: E501
                        },
                    },
                    "required": ["channel_id", "text"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "lucy_send_dm",
                "description": (
                    "Send a direct message to a Slack user. "
                    "Use this for personalized proactive outreach: workflow proposals, "
                    "workflow discovery, follow-ups, or one-on-one insights. "
                    "Only DM when you have something specific and valuable to say. "
                    "Never send generic or vague DMs. "
                    "Limit: max 3 DMs per heartbeat run to avoid spam."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_id": {
                            "type": "string",
                            "description": "Slack user ID to DM (e.g. U0AG8LVAB4M).",
                        },
                        "text": {
                            "type": "string",
                            "description": "Message text. Be personalized and specific -- mention the observed pattern or pain point.",  # noqa: E501
                        },
                    },
                    "required": ["user_id", "text"],
                },
            },
        },
    ]


def is_slack_proactive_tool(tool_name: str) -> bool:
    """Check if a tool name belongs to this module."""
    return tool_name in _VALID_TOOLS


async def execute_slack_proactive_tool(
    tool_name: str,
    parameters: dict[str, Any],
    slack_client: Any,
) -> dict[str, Any]:
    """Execute a proactive Slack tool call.

    Args:
        tool_name: One of the tool names from get_slack_proactive_tool_definitions().
        parameters: Tool parameters.
        slack_client: Slack Bolt async client.

    Returns:
        Result dict with "success" key and optional message.
    """
    if slack_client is None:
        return {"error": "Slack client not available in this context."}

    if tool_name == "lucy_react_to_message":
        return await _react_to_message(parameters, slack_client)

    if tool_name == "lucy_post_to_channel":
        return await _post_to_channel(parameters, slack_client)

    if tool_name == "lucy_send_dm":
        return await _send_dm(parameters, slack_client)

    return {"error": f"Unknown proactive tool: {tool_name}"}


async def _react_to_message(
    parameters: dict[str, Any],
    slack_client: Any,
) -> dict[str, Any]:
    channel_id = parameters.get("channel_id", "")
    message_ts = parameters.get("message_ts", "")
    emoji = parameters.get("emoji", "").strip().strip(":")

    if not channel_id or not message_ts or not emoji:
        return {"error": "channel_id, message_ts, and emoji are required."}

    try:
        await slack_client.reactions_add(
            channel=channel_id,
            timestamp=message_ts,
            name=emoji,
        )
        logger.info(
            "proactive_reaction_added",
            channel=channel_id,
            emoji=emoji,
            message_ts=message_ts,
        )
        return {"success": True, "action": f"Reacted with :{emoji}: to message {message_ts}"}
    except Exception as e:
        err = str(e)
        if "already_reacted" in err:
            return {"success": True, "note": "Already reacted to this message."}
        logger.warning("proactive_reaction_failed", emoji=emoji, error=err)
        return {"error": f"Failed to add reaction: {err[:200]}"}


async def _post_to_channel(
    parameters: dict[str, Any],
    slack_client: Any,
) -> dict[str, Any]:
    channel_id = parameters.get("channel_id", "")
    text = parameters.get("text", "")
    thread_ts = parameters.get("thread_ts")

    if not channel_id or not text:
        return {"error": "channel_id and text are required."}

    try:
        kwargs: dict[str, Any] = {"channel": channel_id, "text": text}
        if thread_ts:
            kwargs["thread_ts"] = thread_ts

        result = await slack_client.chat_postMessage(**kwargs)
        logger.info(
            "proactive_message_posted",
            channel=channel_id,
            text_length=len(text),
            in_thread=bool(thread_ts),
        )
        return {
            "success": True,
            "ts": result.get("ts", ""),
            "channel": channel_id,
        }
    except Exception as e:
        logger.warning("proactive_post_failed", channel=channel_id, error=str(e))
        return {"error": f"Failed to post message: {str(e)[:200]}"}


async def _send_dm(
    parameters: dict[str, Any],
    slack_client: Any,
) -> dict[str, Any]:
    user_id = parameters.get("user_id", "")
    text = parameters.get("text", "")

    if not user_id or not text:
        return {"error": "user_id and text are required."}

    try:
        # Open a DM channel with the user
        dm_result = await slack_client.conversations_open(users=user_id)
        channel_id = dm_result.get("channel", {}).get("id", "")
        if not channel_id:
            return {"error": "Could not open DM channel with user."}

        result = await slack_client.chat_postMessage(channel=channel_id, text=text)
        logger.info(
            "proactive_dm_sent",
            user_id=user_id,
            text_length=len(text),
        )
        return {
            "success": True,
            "ts": result.get("ts", ""),
            "user_id": user_id,
        }
    except Exception as e:
        logger.warning("proactive_dm_failed", user_id=user_id, error=str(e))
        return {"error": f"Failed to send DM: {str(e)[:200]}"}
