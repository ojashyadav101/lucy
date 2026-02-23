"""Slack event handlers for Lucy.

Handles:
- App mentions (@Lucy)
- Direct messages
- Slash commands (/lucy)
- Block Kit actions
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import structlog
from slack_bolt.async_app import AsyncAck, AsyncApp, AsyncBoltContext, AsyncSay

logger = structlog.get_logger()

_processed_events: dict[str, float] = {}
EVENT_DEDUP_TTL = 30.0


def register_handlers(app: AsyncApp) -> None:
    """Register all Slack event handlers with the Bolt app."""

    # ═══ APP MENTION (@Lucy) ════════════════════════════════════════════

    @app.event("app_mention")
    async def handle_app_mention(
        event: dict[str, Any],
        say: AsyncSay,
        context: AsyncBoltContext,
        client: Any,
    ) -> None:
        text = event.get("text", "")
        channel_id = event.get("channel")
        thread_ts = event.get("thread_ts") or event.get("ts")
        event_ts = event.get("ts")

        clean_text = _clean_mention(text)
        if not clean_text.strip():
            await say(text="Hey! How can I help?", thread_ts=thread_ts)
            return

        logger.info(
            "app_mention",
            text=clean_text[:100],
            channel=channel_id,
            workspace_id=context.get("workspace_id", "unknown"),
        )

        await _handle_message(
            text=clean_text,
            channel_id=channel_id,
            thread_ts=thread_ts,
            event_ts=event_ts,
            say=say,
            client=client,
            context=context,
        )

    # ═══ DIRECT MESSAGES & THREAD REPLIES ═══════════════════════════════

    @app.event("message")
    async def handle_message(
        event: dict[str, Any],
        say: AsyncSay,
        context: AsyncBoltContext,
        client: Any,
    ) -> None:
        bot_user_id = context.get("bot_user_id")
        if event.get("user") == bot_user_id:
            return
        subtype = event.get("subtype")
        if subtype in {
            "message_changed", "message_deleted",
            "channel_join", "channel_leave",
        }:
            return

        text = event.get("text", "")
        channel_type = event.get("channel_type")
        channel_id = event.get("channel")
        thread_ts = event.get("thread_ts") or event.get("ts")
        event_ts = event.get("ts")

        if not text.strip():
            return

        if bot_user_id and re.search(rf"<@{bot_user_id}>", text):
            return

        # DMs: always respond
        if channel_type == "im":
            logger.info(
                "direct_message",
                text=text[:100],
                workspace_id=context.get("workspace_id", "unknown"),
            )
            await _handle_message(
                text=text,
                channel_id=channel_id,
                thread_ts=thread_ts,
                event_ts=event_ts,
                say=say,
                client=client,
                context=context,
            )
            return

        # Channel thread replies: respond if Lucy was part of the thread
        if event.get("thread_ts"):
            is_lucy_thread = await _is_lucy_in_thread(
                client, channel_id, event.get("thread_ts")
            )
            if is_lucy_thread:
                await _handle_message(
                    text=text,
                    channel_id=channel_id,
                    thread_ts=thread_ts,
                    event_ts=event_ts,
                    say=say,
                    client=client,
                    context=context,
                )

    # ═══ SLASH COMMANDS ═════════════════════════════════════════════════

    @app.command("/lucy")
    async def handle_slash_command(
        ack: AsyncAck,
        command: dict[str, Any],
        say: AsyncSay,
        context: AsyncBoltContext,
        client: Any,
    ) -> None:
        await ack()

        text = command.get("text", "").strip()
        channel_id = command.get("channel_id")

        logger.info(
            "slash_command",
            args=text,
            workspace_id=context.get("workspace_id", "unknown"),
        )

        if not text or text.lower() == "help":
            await say(text=(
                "*Lucy* — your AI coworker\n\n"
                "Just @mention me or DM me with what you need.\n"
                "I can use tools, search the web, write code, "
                "manage your calendar, and more."
            ))
        elif text.lower() == "status":
            await say(text="I'm online and ready to help.")
        elif text.lower().startswith("connect "):
            provider = text[8:].strip()
            await _handle_connect(context, say, provider)
        else:
            await _handle_message(
                text=text,
                channel_id=channel_id,
                thread_ts=None,
                event_ts=None,
                say=say,
                client=client,
                context=context,
            )

    # ═══ BLOCK KIT ACTIONS ═════════════════════════════════════════════

    @app.action(r"lucy_action_.*")
    async def handle_block_action(
        ack: AsyncAck,
        body: dict[str, Any],
        say: AsyncSay,
        context: AsyncBoltContext,
    ) -> None:
        await ack()

        action = body.get("actions", [{}])[0]
        action_id = action.get("action_id", "")
        value = action.get("value", "")

        logger.info("block_action", action_id=action_id, value=value)
        await say(text=f"Action received: {action_id}")


# ═══════════════════════════════════════════════════════════════════════
# CORE MESSAGE HANDLER
# ═══════════════════════════════════════════════════════════════════════

async def _handle_message(
    text: str,
    channel_id: str | None,
    thread_ts: str | None,
    event_ts: str | None,
    say: AsyncSay,
    client: Any,
    context: AsyncBoltContext,
) -> None:
    """Handle a user message: add reaction, run agent, post response."""
    global _processed_events

    if event_ts:
        now = time.monotonic()
        if event_ts in _processed_events:
            logger.debug("event_dedup_skip", event_ts=event_ts)
            return
        _processed_events[event_ts] = now
        _processed_events = {
            k: v for k, v in _processed_events.items()
            if now - v < EVENT_DEDUP_TTL
        }

    workspace_id = context.get("workspace_id")
    if not workspace_id:
        team_id = context.get("team_id")
        enterprise_id = context.get("enterprise_id")
        workspace_id = str(team_id or enterprise_id or "")
    if not workspace_id:
        logger.error("no_workspace_id", context_keys=list(context.keys()))
        await say(
            text="I couldn't determine your workspace. Please try again.",
            thread_ts=thread_ts,
        )
        return
    workspace_id = str(workspace_id)

    # Add thinking reaction
    if client and channel_id and event_ts:
        asyncio.create_task(_add_reaction(client, channel_id, event_ts))

    from lucy.core.trace import Trace
    trace = Trace.current()

    try:
        from lucy.core.agent import AgentContext, get_agent

        agent = get_agent()
        ctx = AgentContext(
            workspace_id=workspace_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
        )

        response_text = await _run_with_recovery(
            agent, text, ctx, client, workspace_id,
        )

        from lucy.core.output import process_output
        slack_text = process_output(response_text)

        if trace:
            async with trace.span("slack_post"):
                await say(text=slack_text, thread_ts=thread_ts)
        else:
            await say(text=slack_text, thread_ts=thread_ts)

    except Exception as e:
        logger.error(
            "agent_run_failed_all_retries",
            workspace_id=workspace_id,
            error=str(e),
            exc_info=True,
        )
        fallback = (
            "I'm pulling together some details on that — "
            "give me a moment and I'll follow up right here."
        )
        await say(text=fallback, thread_ts=thread_ts)

    finally:
        if client and channel_id and event_ts:
            asyncio.create_task(
                _remove_reaction(client, channel_id, event_ts)
            )


# ═══════════════════════════════════════════════════════════════════════
# SILENT RECOVERY CASCADE
# ═══════════════════════════════════════════════════════════════════════

async def _run_with_recovery(
    agent: Any,
    text: str,
    ctx: Any,
    slack_client: Any,
    workspace_id: str,
) -> str:
    """Run agent with silent retry cascade. Never surface raw errors."""
    from lucy.core.openclaw import OpenClawError

    # Attempt 1: normal run
    try:
        return await agent.run(message=text, ctx=ctx, slack_client=slack_client)
    except OpenClawError as e:
        logger.warning(
            "agent_attempt_1_failed",
            status_code=e.status_code,
            workspace_id=workspace_id,
        )
    except Exception as e:
        logger.warning("agent_attempt_1_error", error=str(e), workspace_id=workspace_id)

    # Attempt 2: wait briefly and retry
    await asyncio.sleep(2)
    try:
        return await agent.run(message=text, ctx=ctx, slack_client=slack_client)
    except OpenClawError as e:
        logger.warning(
            "agent_attempt_2_failed",
            status_code=e.status_code,
            workspace_id=workspace_id,
        )
    except Exception as e:
        logger.warning("agent_attempt_2_error", error=str(e), workspace_id=workspace_id)

    # Attempt 3: downgrade to fast model with simplified prompt
    try:
        from lucy.core.router import MODEL_TIERS
        fast_model = MODEL_TIERS.get("fast", "google/gemini-2.5-flash")
        ctx_simple = type(ctx)(
            workspace_id=ctx.workspace_id,
            channel_id=ctx.channel_id,
            thread_ts=ctx.thread_ts,
        )
        return await agent.run(
            message=text,
            ctx=ctx_simple,
            slack_client=slack_client,
            model_override=fast_model,
        )
    except Exception as e:
        logger.warning("agent_attempt_3_error", error=str(e), workspace_id=workspace_id)

    raise RuntimeError("All recovery attempts exhausted")


# ═══════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════

def _clean_mention(text: str) -> str:
    """Remove @Lucy mention from text."""
    return re.sub(r"<@[A-Z0-9]+>", "", text).strip()


async def _add_reaction(
    client: Any, channel: str, timestamp: str
) -> None:
    try:
        await client.reactions_add(
            channel=channel,
            name="hourglass_flowing_sand",
            timestamp=timestamp,
        )
    except Exception:
        pass


async def _remove_reaction(
    client: Any, channel: str, timestamp: str
) -> None:
    try:
        await client.reactions_remove(
            channel=channel,
            name="hourglass_flowing_sand",
            timestamp=timestamp,
        )
    except Exception:
        pass


async def _is_lucy_in_thread(
    client: Any, channel_id: str | None, thread_ts: str | None
) -> bool:
    """Check if Lucy has previously replied in a thread."""
    if not channel_id or not thread_ts:
        return False
    try:
        result = await client.conversations_replies(
            channel=channel_id, ts=thread_ts, limit=50
        )
        for msg in result.get("messages", []):
            if msg.get("bot_id") or msg.get("app_id"):
                return True
    except Exception:
        pass
    return False


async def _handle_connect(
    context: AsyncBoltContext, say: AsyncSay, provider: str
) -> None:
    """Handle /lucy connect <provider>."""
    workspace_id = str(context.get("workspace_id") or context.get("team_id") or "")
    try:
        from lucy.integrations.composio_client import get_composio_client

        client = get_composio_client()
        url = await client.authorize(
            workspace_id=workspace_id, toolkit=provider
        )
        if url:
            await say(text=(
                f"Please connect *{provider}* here:\n{url}\n\n"
                f"Let me know once you've connected it!"
            ))
        else:
            await say(
                text=f"Couldn't generate a connection link for {provider}."
            )
    except Exception as e:
        logger.error("connect_failed", provider=provider, error=str(e))
        await say(text=f"Failed to connect {provider}.")
