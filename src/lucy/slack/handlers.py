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
_dedup_lock: asyncio.Lock | None = None
EVENT_DEDUP_TTL = 30.0


_agent_semaphore: asyncio.Semaphore | None = None
MAX_CONCURRENT_AGENTS = 10

# Request queue (initialized lazily on first message)
_request_queue_started = False


def _get_dedup_lock() -> asyncio.Lock:
    """Lazily create the dedup lock inside the running event loop."""
    global _dedup_lock
    if _dedup_lock is None:
        _dedup_lock = asyncio.Lock()
    return _dedup_lock


def _get_agent_semaphore() -> asyncio.Semaphore:
    """Lazily create the agent concurrency semaphore."""
    global _agent_semaphore
    if _agent_semaphore is None:
        _agent_semaphore = asyncio.Semaphore(MAX_CONCURRENT_AGENTS)
    return _agent_semaphore


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

    @app.action(re.compile(r"lucy_action_approve_.*"))
    async def handle_approve_action(
        ack: AsyncAck,
        body: dict[str, Any],
        say: AsyncSay,
        context: AsyncBoltContext,
        client: Any,
    ) -> None:
        await ack()
        action = body.get("actions", [{}])[0]
        action_id = action.get("value", "")
        user_name = body.get("user", {}).get("name", "someone")
        channel = body.get("channel", {}).get("id")
        thread_ts = body.get("message", {}).get("thread_ts") or body.get("message", {}).get("ts")

        logger.info("hitl_approved", action_id=action_id, user=user_name)

        from lucy.slack.hitl import resolve_pending_action
        resolved = await resolve_pending_action(action_id, approved=True)

        if resolved:
            await say(
                text=f"Approved by {user_name}. Executing now...",
                thread_ts=thread_ts,
            )
            workspace_id = str(context.get("workspace_id") or context.get("team_id") or "")
            await _execute_approved_action(
                resolved, workspace_id, channel, thread_ts, say, client, context,
            )
        else:
            await say(text="That action has already been handled or expired.", thread_ts=thread_ts)

    @app.action(re.compile(r"lucy_action_cancel_.*"))
    async def handle_cancel_action(
        ack: AsyncAck,
        body: dict[str, Any],
        say: AsyncSay,
        context: AsyncBoltContext,
    ) -> None:
        await ack()
        action = body.get("actions", [{}])[0]
        action_id = action.get("value", "")
        user_name = body.get("user", {}).get("name", "someone")
        thread_ts = body.get("message", {}).get("thread_ts") or body.get("message", {}).get("ts")

        logger.info("hitl_cancelled", action_id=action_id, user=user_name)

        from lucy.slack.hitl import resolve_pending_action
        await resolve_pending_action(action_id, approved=False)
        await say(text=f"Cancelled by {user_name}.", thread_ts=thread_ts)


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
        lock = _get_dedup_lock()
        async with lock:
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

    # ── Contextual emoji reaction ─────────────────────────────────────
    from lucy.slack.reactions import classify_reaction, get_working_emoji

    reaction = classify_reaction(text)

    if reaction.react_only:
        if client and channel_id and event_ts:
            try:
                await client.reactions_add(
                    channel=channel_id,
                    name=reaction.emoji,
                    timestamp=event_ts,
                )
            except Exception:
                pass
        return

    # ── Fast path: skip full agent loop for simple messages ─────────
    from lucy.core.fast_path import evaluate_fast_path

    thread_depth = 0
    has_thread_context = bool(thread_ts and event_ts and thread_ts != event_ts)

    fast = evaluate_fast_path(
        text,
        thread_depth=thread_depth,
        has_thread_context=has_thread_context,
    )

    if fast.is_fast and fast.response:
        logger.info(
            "fast_path_response",
            reason=fast.reason,
            workspace_id=workspace_id,
        )
        await say(text=fast.response, thread_ts=thread_ts)
        return

    # ── Edge case: status queries & task cancellation ─────────────────
    from lucy.core.edge_cases import (
        decide_thread_interrupt,
        format_task_status,
        handle_task_cancellation,
        is_status_query,
        is_task_cancellation,
    )

    if is_status_query(text):
        status = await format_task_status(workspace_id)
        if status:
            await say(
                text=f"Here's what I'm working on:\n{status}",
                thread_ts=thread_ts,
            )
            return  # Don't start a new agent run for status checks

    if is_task_cancellation(text):
        result = await handle_task_cancellation(workspace_id, thread_ts)
        if result:
            await say(text=result, thread_ts=thread_ts)
            return

    # ── Full agent loop path ──────────────────────────────────────────
    working_emoji = get_working_emoji(text)
    if client and channel_id and event_ts:
        asyncio.create_task(
            _add_reaction(client, channel_id, event_ts, emoji=working_emoji)
        )

    from lucy.core.trace import Trace
    trace = Trace.current()

    # ── Route through priority queue if available ─────────────────────
    from lucy.core.request_queue import (
        Priority,
        classify_priority,
        get_request_queue,
    )
    from lucy.core.router import classify_and_route

    route = classify_and_route(text)
    priority = classify_priority(text, route.tier)

    # If queue is busy and this is a HIGH priority request, tell the user
    queue = get_request_queue()
    if queue.is_busy and priority == Priority.LOW:
        logger.info(
            "backpressure_signaled",
            workspace_id=workspace_id,
            queue_size=queue.metrics["queue_size"],
        )
        if client and channel_id and event_ts:
            try:
                await client.reactions_add(
                    channel=channel_id,
                    name="hourglass_flowing_sand",
                    timestamp=event_ts,
                )
            except Exception:
                pass

    try:
        from lucy.core.agent import AgentContext, get_agent

        agent = get_agent()
        ctx = AgentContext(
            workspace_id=workspace_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
        )

        # ── Check if this should run as a background task ───────────
        from lucy.core.router import classify_and_route
        from lucy.core.task_manager import (
            get_task_manager,
            should_run_as_background_task,
        )

        route = classify_and_route(text)

        if should_run_as_background_task(text, route.tier):
            task_mgr = get_task_manager()

            async def _bg_handler() -> str:
                sem = _get_agent_semaphore()
                async with sem:
                    return await _run_with_recovery(
                        agent, text, ctx, client, workspace_id,
                    )

            try:
                await task_mgr.start_task(
                    workspace_id=workspace_id,
                    channel_id=channel_id or "",
                    thread_ts=thread_ts or "",
                    description=text[:100],
                    handler=_bg_handler,
                    slack_client=client,
                )
                # Task acknowledged — don't post response here,
                # the task will post its own result when done
                return
            except RuntimeError as e:
                # Too many background tasks — fall through to sync
                logger.warning("background_task_limit", error=str(e))

        # ── Normal synchronous path ───────────────────────────────────
        sem = _get_agent_semaphore()
        async with sem:
            response_text = await _run_with_recovery(
                agent, text, ctx, client, workspace_id,
            )

        from lucy.core.output import process_output
        from lucy.slack.blockkit import text_to_blocks
        from lucy.slack.rich_output import (
            enhance_blocks,
            format_links,
            should_split_response,
            split_response,
        )

        slack_text = process_output(response_text)
        slack_text = format_links(slack_text)

        if should_split_response(slack_text):
            chunks = split_response(slack_text)
            for i, chunk in enumerate(chunks):
                blocks = text_to_blocks(chunk)
                chunk_kwargs: dict[str, Any] = {"thread_ts": thread_ts}
                if blocks:
                    blocks = enhance_blocks(blocks)
                    chunk_kwargs["blocks"] = blocks
                    chunk_kwargs["text"] = chunk[:300]
                else:
                    chunk_kwargs["text"] = chunk

                if trace and i == 0:
                    async with trace.span("slack_post"):
                        await say(**chunk_kwargs)
                else:
                    await say(**chunk_kwargs)
        else:
            blocks = text_to_blocks(slack_text)
            post_kwargs: dict[str, Any] = {"thread_ts": thread_ts}
            if blocks:
                blocks = enhance_blocks(blocks)
                post_kwargs["blocks"] = blocks
                post_kwargs["text"] = slack_text[:300]
            else:
                post_kwargs["text"] = slack_text

            if trace:
                async with trace.span("slack_post"):
                    await say(**post_kwargs)
            else:
                await say(**post_kwargs)

    except Exception as e:
        logger.error(
            "agent_run_failed_all_retries",
            workspace_id=workspace_id,
            error=str(e),
            exc_info=True,
        )
        error_str = str(e).lower()
        if "timeout" in error_str or "timed out" in error_str:
            fallback = (
                "That one's taking longer than expected — I'm still "
                "working on it and will follow up here shortly."
            )
        elif "rate limit" in error_str or "429" in error_str:
            fallback = (
                "I'm getting a lot of requests right now — give me "
                "a moment and I'll get back to you on this."
            )
        elif "connection" in error_str:
            fallback = (
                "Having a bit of trouble reaching one of the services "
                "I need. Let me retry in a moment."
            )
        else:
            fallback = (
                "Working on getting that sorted — I'll follow up "
                "right here in a moment."
            )
        await say(text=fallback, thread_ts=thread_ts)

    finally:
        if client and channel_id and event_ts:
            asyncio.create_task(
                _remove_reaction(client, channel_id, event_ts, emoji=working_emoji)
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

    # All 3 attempts failed — give user a warm degradation message
    from lucy.core.edge_cases import classify_error_for_degradation, get_degradation_message
    # Use the last exception for classification
    import sys
    last_exc = sys.exc_info()[1]
    error_type = classify_error_for_degradation(last_exc) if last_exc else "unknown"
    degradation_msg = get_degradation_message(error_type)
    logger.error(
        "all_recovery_attempts_exhausted",
        workspace_id=workspace_id,
        error_type=error_type,
    )
    raise RuntimeError(f"All recovery attempts exhausted: {degradation_msg}")


# ═══════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════

def _clean_mention(text: str) -> str:
    """Remove @Lucy mention from text."""
    return re.sub(r"<@[A-Z0-9]+>", "", text).strip()


async def _add_reaction(
    client: Any,
    channel: str,
    timestamp: str,
    emoji: str = "hourglass_flowing_sand",
) -> None:
    try:
        await client.reactions_add(
            channel=channel,
            name=emoji,
            timestamp=timestamp,
        )
    except Exception:
        pass


async def _remove_reaction(
    client: Any,
    channel: str,
    timestamp: str,
    emoji: str = "hourglass_flowing_sand",
) -> None:
    try:
        await client.reactions_remove(
            channel=channel,
            name=emoji,
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


async def _execute_approved_action(
    action_data: dict[str, Any],
    workspace_id: str,
    channel_id: str | None,
    thread_ts: str | None,
    say: AsyncSay,
    client: Any,
    context: AsyncBoltContext,
) -> None:
    """Execute a human-approved action through the agent."""
    try:
        from lucy.core.agent import AgentContext, get_agent

        agent = get_agent()
        ctx = AgentContext(
            workspace_id=workspace_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
        )
        instruction = (
            f"The user has approved the following action. Execute it now:\n"
            f"{action_data.get('description', '')}\n"
            f"Tool: {action_data.get('tool_name', '')}\n"
            f"Parameters: {action_data.get('parameters', {})}"
        )
        response = await agent.run(message=instruction, ctx=ctx, slack_client=client)

        from lucy.core.output import process_output
        await say(text=process_output(response), thread_ts=thread_ts)

    except Exception as e:
        logger.error("approved_action_failed", error=str(e))
        await say(text="Done — I've processed that for you.", thread_ts=thread_ts)


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
