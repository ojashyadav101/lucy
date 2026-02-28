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

from lucy.config import LLMPresets, settings

logger = structlog.get_logger()

_processed_events: dict[str, float] = {}
_dedup_lock: asyncio.Lock | None = None
EVENT_DEDUP_TTL = settings.event_dedup_ttl
HANDLER_EXECUTION_TIMEOUT = settings.handler_execution_timeout
APPROVED_ACTION_TIMEOUT = settings.approved_action_timeout


_agent_semaphore: asyncio.Semaphore | None = None
MAX_CONCURRENT_AGENTS = settings.max_concurrent_agents

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
            text=clean_text[:300],
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
                text=text[:300],
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
                client, channel_id, event.get("thread_ts"),
                lucy_bot_id=bot_user_id or "",
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
# INITIAL ACKNOWLEDGMENT
# ═══════════════════════════════════════════════════════════════════════

_ACK_SYSTEM_PROMPT = """\
You are Lucy, a sharp AI coworker. The user just sent a request that will \
take you a while to complete. Write a 1-2 sentence acknowledgment that:

1. References the SPECIFIC task they asked for (not generic)
2. Shows genuine enthusiasm or curiosity about the request
3. Gives a rough time hint ("give me a few minutes", "should have this shortly")
4. Sounds like a real colleague on Slack, not a bot

RULES:
- NEVER start with "Got it" or "On it"
- NEVER use phrases like "working on this now" or "I'll get right on that"
- Be specific to what they asked. If they want an app, mention what kind. \
If they want data, mention what data.
- Use 1 emoji max, only if it fits naturally
- Keep it under 40 words
- Use contractions. Sound human.
- Match the energy: if they're excited, match it. If it's routine, be chill.
- NEVER promise to "dive into" or "pull" data from a service unless that \
service is listed in the CONNECTED INTEGRATIONS below. If the service isn't \
listed, use hedging language like "Let me check if I have access to..." or \
"Let me see what I can pull up..." instead of promising results.

{connected_services_note}

Examples of GOOD acknowledgments:
- "Love this idea 🌙 Building you a lunar cycle tracker with a handcrafted feel. Give me a few minutes."
- "Pulling your Stripe revenue data now, I'll have the breakdown with trends in a couple minutes."
- "Interesting comparison. Let me dig into the latest pricing and features across all three."

Examples of BAD acknowledgments (never do these):
- "Got it, working on this now."
- "On it — I'll build this for you."
- "I'll get right on that!"
- "Sure thing! Let me work on this."
- "Ooh, I love analyzing X data! I'll dive in." (then failing because the service isn't connected)

Output ONLY the acknowledgment text. Nothing else."""


_SKIP_ACK_INTENTS = frozenset({"greeting", "conversational", "lookup"})

_FAST_ACK_INTENTS = frozenset({
    "code", "code_reasoning", "data", "document", "research",
    "monitoring", "tool_use",
})


async def _build_acknowledgment(
    text: str,
    intent: str,
    connected_services: list[str] | None = None,
) -> str | None:
    """Generate a context-aware acknowledgment via a fast LLM call.

    Returns None for tasks that are likely <30s (no ack needed).
    Falls back to a minimal static message if the LLM call fails.
    """
    if intent in _SKIP_ACK_INTENTS:
        return None

    word_count = len(text.split())
    if word_count < 5 and intent == "tool_use":
        return None

    if intent not in _FAST_ACK_INTENTS:
        return None

    try:
        from lucy.core.openclaw import ChatConfig, get_openclaw_client

        client = await get_openclaw_client()

        if connected_services:
            services_note = (
                f"CONNECTED INTEGRATIONS: {', '.join(connected_services)}\n"
                f"Only these services are available. If the user asks about a "
                f"service NOT in this list, do NOT promise to pull its data."
            )
        else:
            services_note = (
                "CONNECTED INTEGRATIONS: unknown — use hedging language like "
                "'Let me check what I can access...' instead of promising results."
            )

        system_prompt = _ACK_SYSTEM_PROMPT.format(
            connected_services_note=services_note,
        )

        user_msg = (
            f"User's request (intent: {intent}):\n"
            f"{text[:300]}"
        )

        response = await asyncio.wait_for(
            client.chat_completion(
                messages=[{"role": "user", "content": user_msg}],
                config=ChatConfig(
                    model=settings.model_tier_fast,
                    system_prompt=system_prompt,
                    max_tokens=LLMPresets.ACK.max_tokens,
                    temperature=LLMPresets.ACK.temperature,
                ),
            ),
            timeout=3.0,
        )

        result = (response.content or "").strip()
        if result and 10 < len(result) < 200:
            return result
    except Exception as exc:
        logger.debug("ack_llm_failed", error=str(exc))

    return None


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
            except Exception as e:
                logger.warning("slack_reaction_add_failed", error=str(e))
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
    acquired = False
    tlock = None
    if effective_thread:
        tlock = await _get_thread_lock(effective_thread)
        try:
            acquired = await asyncio.wait_for(tlock.acquire(), timeout=0.1)
        except asyncio.TimeoutError:
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
        reaction_task = asyncio.create_task(
            _add_reaction(client, channel_id, event_ts, emoji=working_emoji)
        )
        reaction_task.add_done_callback(_log_task_exception)

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

    # ── Immediate acknowledgment for complex tasks ────────────────
    is_thread_reply = thread_ts and event_ts and thread_ts != event_ts
    is_short_followup = is_thread_reply and len(text.split()) < 12
    should_ack = (
        route.intent in _FAST_ACK_INTENTS
        and client
        and channel_id
        and not is_short_followup
    )

    if should_ack:
        async def _send_ack() -> None:
            svc_names: list[str] | None = None
            try:
                from lucy.integrations.composio_client import get_composio_client
                from lucy.integrations.wrapper_generator import (
                    discover_saved_wrappers,
                )
                composio = get_composio_client()
                svc_names = await asyncio.wait_for(
                    composio.get_connected_app_names_reliable(workspace_id),
                    timeout=2.0,
                )
                for w in discover_saved_wrappers():
                    svc = w.get("service_name", "")
                    if svc and svc_names and svc not in svc_names:
                        svc_names.append(svc)
            except Exception:
                pass
            ack_msg = await _build_acknowledgment(
                text, route.intent, connected_services=svc_names,
            )
            if ack_msg:
                await say(text=ack_msg, thread_ts=thread_ts)

        ack_task = asyncio.create_task(_send_ack())
        ack_task.add_done_callback(_log_task_exception)

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
            except Exception as e:
                logger.warning("slack_hourglass_reaction_add_failed", error=str(e))

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
            except Exception as e:
                logger.warning(
                    "composio_setup_failed",
                    workspace_id=workspace_id,
                    error=str(e),
                )

        # ── Auto-register channel metadata (purpose / DM flag) ───────
        if channel_id and workspace_id:
            registration_task = asyncio.create_task(
                _register_channel_background(client, workspace_id, channel_id)
            )
            registration_task.add_done_callback(_log_task_exception)

        # ── Check if this should run as a background task ───────────
        from lucy.core.task_manager import (
            get_task_manager,
            should_run_as_background_task,
        )

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
                    description=text[:300],
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

        try:
            response_text = await asyncio.wait_for(
                _sync_run(), timeout=HANDLER_EXECUTION_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.error(
                "handler_execution_timeout",
                workspace_id=workspace_id,
                timeout=HANDLER_EXECUTION_TIMEOUT,
            )
            await say(
                text="That request took too long to process. "
                "Want me to give it another try?",
                thread_ts=thread_ts,
            )
            return

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
            if not response_text:
                logger.error(
                    "empty_agent_response",
                    workspace_id=workspace_id,
                    text=text[:300],
                )
            slack_text = response_text or (
                "I processed your request but didn't generate a response. "
                "Could you rephrase or provide more details?"
            )

        if should_split_response(slack_text):
            chunks = split_response(slack_text)
            for i, chunk in enumerate(chunks):
                blocks = text_to_blocks(chunk)
                chunk_kwargs: dict[str, Any] = {"thread_ts": thread_ts}
                if blocks:
                    blocks = enhance_blocks(blocks)
                    chunk_kwargs["blocks"] = blocks
                    chunk_kwargs["text"] = chunk[:500]
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
                post_kwargs["text"] = slack_text[:500]
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
                "A service I was reaching out to was slow. "
                "Want me to give it another try?"
            )
        elif "rate limit" in error_str or "429" in error_str:
            fallback = (
                "I'm getting rate limited right now. "
                "I'll be ready again in a moment."
            )
        else:
            fallback = (
                "I ran into an issue I couldn't work around. "
                "Want me to try again?"
            )
        await say(text=fallback, thread_ts=thread_ts)

    finally:
        if acquired and tlock is not None:
            tlock.release()
        if client and channel_id and event_ts:
            cleanup_task = asyncio.create_task(
                _remove_reaction(client, channel_id, event_ts, emoji=working_emoji)
            )
            cleanup_task.add_done_callback(_log_task_exception)


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

    def _is_client_error(error_str: str) -> bool:
        return any(
            code in error_str for code in ("400", "401", "403", "404", "405", "422")
        )

    def _retry_delay(error_str: str) -> float:
        lower = error_str.lower()
        if "rate limit" in lower or "429" in lower:
            return 15.0
        if "timeout" in lower:
            return 3.0
        return 2.0

    # Attempt 1: normal run (router-selected model)
    last_exc: Exception | None = None
    try:
        return await agent.run(message=text, ctx=ctx, slack_client=slack_client)
    except OpenClawError as e:
        last_error = f"OpenClaw error (status {e.status_code}): {e}"
        last_exc = e
        logger.warning(
            "agent_attempt_1_failed",
            status_code=e.status_code,
            workspace_id=workspace_id,
        )
        if _is_client_error(str(e.status_code)):
            raise
    except Exception as e:
        last_error = str(e)
        last_exc = e
        logger.warning("agent_attempt_1_error", error=str(e), workspace_id=workspace_id)
        if _is_client_error(last_error):
            raise

    # Attempt 2: single retry with failure context (skip for client errors)
    await asyncio.sleep(_retry_delay(last_error))
    try:
        return await agent.run(
            message=text,
            ctx=ctx,
            slack_client=slack_client,
            failure_context=f"Previous attempt failed: {last_error}",
        )
    except Exception as e:
        last_exc = e
        logger.warning("agent_attempt_2_error", error=str(e), workspace_id=workspace_id)

    from lucy.pipeline.edge_cases import classify_error_for_degradation, get_degradation_message
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

def _log_task_exception(task: asyncio.Task) -> None:  # type: ignore[type-arg]
    if not task.cancelled() and task.exception():
        logger.warning("background_task_failed", error=str(task.exception()))


def _clean_mention(text: str) -> str:
    """Remove @Lucy mention from text."""
    return re.sub(r"<@[A-Z0-9]+>", "", text).strip()


async def _register_channel_background(
    client: Any,
    workspace_id: str,
    channel_id: str,
) -> None:
    """Silently fetch and register channel metadata in the background.

    Only fetches if the channel isn't already registered (cheap disk check).
    """
    try:
        from lucy.workspace.filesystem import get_workspace
        from lucy.workspace.channel_registry import (
            get_channel_context,
            register_channel,
        )

        ws = get_workspace(workspace_id)
        existing = get_channel_context(ws, channel_id)

        is_dm = channel_id.startswith("D")
        if is_dm:
            register_channel(ws, channel_id, is_dm=True)
            return

        # Skip re-fetching if already registered recently
        if existing.get("name") and existing.get("last_seen"):
            return

        result = await client.conversations_info(channel=channel_id)
        ch = result.get("channel", {})
        register_channel(
            ws,
            channel_id=channel_id,
            name=ch.get("name", ""),
            purpose=ch.get("purpose", {}).get("value", ""),
            topic=ch.get("topic", {}).get("value", ""),
            is_private=ch.get("is_private", False),
            is_dm=ch.get("is_im", False),
        )
    except Exception as e:
        logger.warning(
            "channel_registration_failed",
            workspace_id=workspace_id,
            channel_id=channel_id,
            error=str(e),
        )


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
    except Exception as e:
        logger.warning("slack_reaction_add_failed", emoji=emoji, error=str(e))


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
    except Exception as e:
        logger.warning("slack_reaction_remove_failed", emoji=emoji, error=str(e))


async def _is_lucy_in_thread(
    client: Any,
    channel_id: str | None,
    thread_ts: str | None,
    lucy_bot_id: str = "",
) -> bool:
    """Check if Lucy has previously replied in a thread."""
    if not channel_id or not thread_ts:
        return False
    try:
        result = await client.conversations_replies(
            channel=channel_id, ts=thread_ts, limit=50
        )
        for msg in result.get("messages", []):
            if lucy_bot_id and (
                msg.get("user") == lucy_bot_id
                or msg.get("bot_id") == lucy_bot_id
            ):
                return True
            if not lucy_bot_id and (msg.get("bot_id") or msg.get("app_id")):
                return True
    except Exception as e:
        logger.warning("thread_history_check_failed", error=str(e))
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
    tool_name = action_data.get("tool_name", "unknown")
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
            f"Tool: {tool_name}\n"
            f"Parameters: {action_data.get('parameters', {})}"
        )
        response = await asyncio.wait_for(
            agent.run(message=instruction, ctx=ctx, slack_client=client),
            timeout=APPROVED_ACTION_TIMEOUT,
        )

        from lucy.pipeline.output import process_output
        output = await process_output(response)
        if not output or not output.strip():
            output = "The action completed but produced no visible output."
        await say(text=output, thread_ts=thread_ts)

    except asyncio.TimeoutError:
        logger.error(
            "approved_action_timeout",
            tool=tool_name,
            timeout=APPROVED_ACTION_TIMEOUT,
            workspace_id=workspace_id,
        )
        await say(
            text=f"The approved action '{tool_name}' timed out after "
            f"{APPROVED_ACTION_TIMEOUT}s. Want me to try again?",
            thread_ts=thread_ts,
        )
    except Exception as e:
        logger.error(
            "approved_action_failed",
            tool=tool_name,
            error=str(e),
            workspace_id=workspace_id,
        )
        await say(
            text=f"The approved action '{tool_name}' failed: {e}. "
            "Want me to try again?",
            thread_ts=thread_ts,
        )


async def _handle_connect(
    context: AsyncBoltContext, say: AsyncSay, provider: str
) -> None:
    """Handle /lucy connect <provider>."""
    workspace_id = str(context.get("workspace_id") or context.get("team_id") or "")
    try:
        from lucy.integrations.composio_client import get_composio_client

        client = get_composio_client()
        auth_result = await client.authorize(
            workspace_id=workspace_id, toolkit=provider
        )
        url = auth_result.get("url")
        if url:
            from lucy.pipeline.humanize import humanize as _hz
            msg = await _hz(
                f"Give the user a connection link for {provider}. "
                f"The link is: {url} — tell them to click it to "
                f"authorize, and let you know when done.",
            )
            await say(text=msg)
            await client.invalidate_cache(workspace_id)
        else:
            auth_error = auth_result.get("error", "unknown error")
            from lucy.pipeline.humanize import humanize as _hz
            msg = await _hz(
                f"You couldn't generate a connection link for {provider} "
                f"({auth_error}). "
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
