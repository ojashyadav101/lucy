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

_request_queue_started = False

_thread_locks: dict[str, asyncio.Lock] = {}
_thread_lock_registry: asyncio.Lock | None = None


def _get_thread_lock_registry() -> asyncio.Lock:
    global _thread_lock_registry
    if _thread_lock_registry is None:
        _thread_lock_registry = asyncio.Lock()
    return _thread_lock_registry


async def _get_thread_lock(thread_ts: str) -> asyncio.Lock:
    """Get or create a per-thread lock to prevent concurrent agent runs."""
    registry = _get_thread_lock_registry()
    async with registry:
        now = time.monotonic()
        stale = [
            k for k, v in _thread_locks.items()
            if not v.locked() and hasattr(v, "_created_at")
            and now - v._created_at > 300  # type: ignore[attr-defined]
        ]
        for k in stale:
            del _thread_locks[k]

        if thread_ts not in _thread_locks:
            lock = asyncio.Lock()
            lock._created_at = now  # type: ignore[attr-defined]
            _thread_locks[thread_ts] = lock
        return _thread_locks[thread_ts]


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
            from lucy.pipeline.humanize import pick
            await say(text=pick("greeting"), thread_ts=thread_ts)
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
            user_id=event.get("user"),
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
                user_id=event.get("user"),
            )
            return

        # Channel thread replies: respond if Lucy was part of the thread
        if event.get("thread_ts"):
            # If there's an active background task in this thread,
            # don't start a new agent run that would re-do the work.
            from lucy.core.task_manager import get_task_manager

            task_mgr = get_task_manager()
            active = task_mgr.get_active_for_thread(
                event.get("thread_ts"),
            )
            if active:
                from lucy.pipeline.edge_cases import is_task_cancellation

                if is_task_cancellation(text):
                    await task_mgr.cancel_task(active.task_id)
                    from lucy.pipeline.humanize import pick
                    await say(
                        text=pick("task_cancelled"),
                        thread_ts=thread_ts,
                    )
                else:
                    elapsed = int(time.monotonic() - active.started_at)
                    from lucy.pipeline.humanize import humanize
                    msg = await humanize(
                        f"Let the user know their task is still running "
                        f"(about {elapsed} seconds in) and you'll post "
                        f"the results when done.",
                    )
                    await say(text=msg, thread_ts=thread_ts)
                return

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
                    user_id=event.get("user"),
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
            from lucy.pipeline.humanize import pick
            await say(text=pick("help"))
        elif text.lower() == "status":
            from lucy.pipeline.humanize import pick
            await say(text=pick("status"))
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
                user_id=command.get("user_id"),
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
            from lucy.pipeline.humanize import pick
            await say(
                text=pick("hitl_approved", user=user_name),
                thread_ts=thread_ts,
            )
            workspace_id = str(context.get("workspace_id") or context.get("team_id") or "")
            await _execute_approved_action(
                resolved, workspace_id, channel, thread_ts, say, client, context,
            )
        else:
            from lucy.pipeline.humanize import pick
            await say(text=pick("hitl_expired"), thread_ts=thread_ts)

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
        from lucy.pipeline.humanize import pick
        await say(text=pick("hitl_cancelled", user=user_name), thread_ts=thread_ts)


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
    user_id: str | None = None,
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
        from lucy.pipeline.humanize import humanize
        msg = await humanize(
            "Tell the user you had trouble identifying their workspace "
            "and ask them to try again."
        )
        await say(text=msg, thread_ts=thread_ts)
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
    from lucy.pipeline.fast_path import evaluate_fast_path

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
    from lucy.pipeline.edge_cases import (
        decide_thread_interrupt,
        format_task_status,
        handle_task_cancellation,
        is_status_query,
        is_task_cancellation,
    )

    if is_status_query(text):
        status = await format_task_status(workspace_id)
        if status:
            from lucy.pipeline.humanize import humanize as _hz
            msg = await _hz(
                f"Summarize your current work status for the user. "
                f"Active tasks: {status}",
            )
            await say(text=msg, thread_ts=thread_ts)
            return

    if is_task_cancellation(text):
        result = await handle_task_cancellation(workspace_id, thread_ts)
        if result:
            from lucy.pipeline.humanize import pick
            await say(text=pick("task_cancelled"), thread_ts=thread_ts)
            return

    # ── Thread lock: prevent concurrent agent runs in same thread ─────
    effective_thread = thread_ts or event_ts
    if effective_thread:
        tlock = await _get_thread_lock(effective_thread)
        if tlock.locked():
            logger.info(
                "thread_busy_skipped",
                thread_ts=effective_thread,
                workspace_id=workspace_id,
            )
            from lucy.pipeline.humanize import humanize
            msg = await humanize(
                "Let the user know you're still working on their "
                "previous request in this thread and will get back "
                "to them shortly. Be warm and brief.",
            )
            await say(text=msg, thread_ts=thread_ts)
            return

    # ── Full agent loop path ──────────────────────────────────────────
    working_emoji = get_working_emoji(text)
    if client and channel_id and event_ts:
        asyncio.create_task(
            _add_reaction(client, channel_id, event_ts, emoji=working_emoji)
        )

    from lucy.infra.trace import Trace
    trace = Trace.current()

    # ── Route through priority queue if available ─────────────────────
    from lucy.infra.request_queue import (
        Priority,
        classify_priority,
        get_request_queue,
    )
    from lucy.pipeline.router import classify_and_route

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
        team_id = str(context.get("team_id") or "")
        ctx = AgentContext(
            workspace_id=workspace_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            user_slack_id=user_id,
            team_id=team_id,
        )

        if team_id and workspace_id:
            try:
                from lucy.integrations.composio_client import get_composio_client
                composio = get_composio_client()
                composio.set_entity_id(workspace_id, f"slack_{team_id}")
            except Exception:
                pass

        # ── Check if this should run as a background task ───────────
        from lucy.pipeline.router import classify_and_route
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

        # ── Normal synchronous path (thread-locked) ─────────────────
        async def _sync_run() -> str:
            sem = _get_agent_semaphore()
            async with sem:
                return await _run_with_recovery(
                    agent, text, ctx, client, workspace_id,
                )

        if effective_thread:
            tlock = await _get_thread_lock(effective_thread)
            async with tlock:
                response_text = await _sync_run()
        else:
            response_text = await _sync_run()

        from lucy.pipeline.output import process_output
        from lucy.slack.blockkit import text_to_blocks
        from lucy.slack.rich_output import (
            enhance_blocks,
            format_links,
            should_split_response,
            split_response,
        )

        response_text = response_text or ""
        slack_text = await process_output(response_text)
        slack_text = format_links(slack_text or "")
        if not slack_text:
            slack_text = response_text or "Done!"

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
        from lucy.pipeline.humanize import pick

        task_hint = ""
        if text:
            snippet = text.strip()[:80]
            if len(snippet) > 60:
                snippet = snippet[:57] + "..."
            task_hint = f' while working on "{snippet}"'

        error_str = str(e).lower()
        if "timeout" in error_str or "timed out" in error_str:
            fallback = (
                f"A service I was reaching out to didn't respond in time{task_hint}. "
                "Want me to give it another try?"
            )
        elif "rate limit" in error_str or "429" in error_str:
            pool_msg = pick("error_rate_limit")
            fallback = (
                f"Getting a lot of requests right now, "
                f"I'll retry{task_hint} in a moment."
                if task_hint
                else pool_msg
            )
        elif "connection" in error_str:
            pool_msg = pick("error_connection")
            fallback = (
                f"Had trouble reaching an external service{task_hint}. "
                "Let me give it another shot."
                if task_hint
                else pool_msg
            )
        else:
            pool_msg = pick("error_generic")
            fallback = (
                f"I hit an unexpected issue{task_hint}. "
                "Let me try a different approach."
                if task_hint
                else pool_msg
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
    """Run agent with a single retry on exception.

    The supervisor inside the agent loop handles re-planning and
    course-correction during execution. This outer layer only catches
    hard crashes (network errors, 5xx responses) and does ONE retry
    with failure context so the agent can adapt.
    """
    from lucy.core.openclaw import OpenClawError

    # Attempt 1: normal run (router-selected model)
    try:
        return await agent.run(message=text, ctx=ctx, slack_client=slack_client)
    except OpenClawError as e:
        last_error = f"OpenClaw error (status {e.status_code}): {e}"
        logger.warning(
            "agent_attempt_1_failed",
            status_code=e.status_code,
            workspace_id=workspace_id,
        )
    except Exception as e:
        last_error = str(e)
        logger.warning("agent_attempt_1_error", error=str(e), workspace_id=workspace_id)

    # Attempt 2: single retry with failure context
    await asyncio.sleep(2)
    try:
        return await agent.run(
            message=text,
            ctx=ctx,
            slack_client=slack_client,
            failure_context=f"Previous attempt failed: {last_error}",
        )
    except Exception as e:
        logger.warning("agent_attempt_2_error", error=str(e), workspace_id=workspace_id)

    from lucy.pipeline.edge_cases import classify_error_for_degradation, get_degradation_message
    import sys
    last_exc = sys.exc_info()[1]
    error_type = classify_error_for_degradation(last_exc) if last_exc else "unknown"
    degradation_msg = get_degradation_message(error_type)
    logger.error(
        "recovery_attempts_exhausted",
        workspace_id=workspace_id,
        error_type=error_type,
    )
    raise RuntimeError(f"Recovery attempts exhausted: {degradation_msg}")


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

        from lucy.pipeline.output import process_output
        await say(text=await process_output(response), thread_ts=thread_ts)

    except Exception as e:
        logger.error("approved_action_failed", error=str(e))
        from lucy.pipeline.humanize import pick
        await say(text=pick("error_generic"), thread_ts=thread_ts)


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
            from lucy.pipeline.humanize import humanize as _hz
            msg = await _hz(
                f"Give the user a connection link for {provider}. "
                f"The link is: {url} — tell them to click it to "
                f"authorize, and let you know when done.",
            )
            await say(text=msg)
            client.invalidate_cache(workspace_id)
        else:
            from lucy.pipeline.humanize import humanize as _hz
            msg = await _hz(
                f"You couldn't generate a connection link for {provider}. "
                f"Suggest the user ask you in a regular message instead "
                f"so you can search for the right integration name.",
            )
            await say(text=msg)
    except Exception as e:
        logger.error("connect_failed", provider=provider, error=str(e))
        from lucy.pipeline.humanize import humanize as _hz
        msg = await _hz(
            f"You had trouble connecting {provider}. Suggest the user "
            f"ask you directly in a conversation so you can find the "
            f"right integration.",
        )
        await say(text=msg)
