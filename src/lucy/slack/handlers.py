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

from lucy.config import settings

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

    # ── HITL expiry notification ────────────────────────────────────────
    # When a pending action times out, notify the requesting user so they
    # know to re-issue the request rather than waiting forever.
    async def _on_hitl_expired(action: dict[str, Any]) -> None:
        requesting_user = action.get("requesting_user_id", "")
        tool_name = action.get("tool_name", "action")
        if not requesting_user:
            return
        try:
            description = action.get("description", "")
            short_desc = description.split("\n")[0][:120] if description else tool_name
            await app.client.chat_postMessage(
                channel=requesting_user,
                text=(
                    f"⏰ Your approval request for *{short_desc}* has expired "
                    f"(requests expire after {int(PENDING_TTL_SECONDS // 60)} minutes). "
                    f"Re-send your original message if you still want me to do this."
                ),
            )
        except Exception as exc:
            logger.debug("hitl_expiry_notify_failed", error=str(exc))

    from lucy.slack.hitl import register_expiry_callback, PENDING_TTL_SECONDS
    register_expiry_callback(_on_hitl_expired)

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
            "bot_message",
        }:
            return
        # Skip messages posted by any bot (including Lucy herself) to prevent
        # Lucy from reacting to her own HITL prompts or other bot replies.
        if event.get("bot_id"):
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
                workspace_id=context.get("workspace_id", ""),
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
            return

        # ── Implicit mention detection (no @, no thread, channel msg) ───
        # Two-layer: free regex gate → cheap LLM classifier.
        # Only fires for channel messages that say "lucy" without @mention.
        from lucy.slack.implicit_mention import (
            should_respond_to_implicit_mention,
        )

        should_respond = await should_respond_to_implicit_mention(
            text=text,
            channel_id=channel_id,
            client=client,
            channel_id_for_context=channel_id,
            event_ts=event_ts,
        )

        if should_respond:
            logger.info(
                "implicit_mention_triggered",
                text=text[:300],
                channel=channel_id,
                workspace_id=context.get("workspace_id", "unknown"),
            )
            clean_text = re.sub(
                r"\blucy\b", "", text, flags=re.IGNORECASE,
            ).strip()
            if not clean_text:
                clean_text = text
            await _handle_message(
                text=clean_text,
                channel_id=channel_id,
                thread_ts=event_ts,  # start a new thread from this message
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
        approver_id = body.get("user", {}).get("id", "")
        user_name = body.get("user", {}).get("name", "someone")
        channel = body.get("channel", {}).get("id")
        message_ts = body.get("message", {}).get("ts")
        thread_ts = body.get("message", {}).get("thread_ts") or message_ts

        logger.info("hitl_approved", action_id=action_id, user=user_name)

        from lucy.slack.hitl import resolve_pending_action

        # Ownership check: only the user who triggered the action can approve it.
        # This prevents other workspace members from executing actions on behalf
        # of someone else without their knowledge.
        from lucy.slack.hitl import get_pending_action_metadata
        metadata = get_pending_action_metadata(action_id)
        if metadata:
            requesting_user = metadata.get("requesting_user_id", "")
            if requesting_user and approver_id and requesting_user != approver_id:
                logger.warning(
                    "hitl_unauthorized_approval_attempt",
                    action_id=action_id,
                    requesting_user=requesting_user,
                    approver_id=approver_id,
                )
                if channel and message_ts:
                    original_blocks = body.get("message", {}).get("blocks", [])
                    updated_blocks = [b for b in original_blocks if b.get("type") != "actions"]
                    updated_blocks.append({
                        "type": "context",
                        "elements": [{"type": "mrkdwn", "text": "⚠️  Only the person who requested this action can approve it."}],
                    })
                    try:
                        await client.chat_update(
                            channel=channel, ts=message_ts,
                            blocks=updated_blocks, text="Unauthorized approval attempt.",
                        )
                    except Exception:
                        pass
                return

        resolved = await resolve_pending_action(action_id, approved=True)

        if resolved:
            # Update the approval message in place: remove the buttons, show status.
            # This gives immediate visual feedback without a separate reply.
            if channel and message_ts:
                original_blocks = body.get("message", {}).get("blocks", [])
                updated_blocks = [b for b in original_blocks if b.get("type") != "actions"]
                updated_blocks.append({
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": f"✓  Approved by *{user_name}* — on it!"},
                    ],
                })
                try:
                    await client.chat_update(
                        channel=channel,
                        ts=message_ts,
                        blocks=updated_blocks,
                        text="Approved — executing now.",
                    )
                except Exception as _upd_err:
                    logger.warning("hitl_approve_message_update_failed", error=str(_upd_err))

            workspace_id = str(context.get("workspace_id") or context.get("team_id") or "")
            await _execute_approved_action(
                resolved, workspace_id, channel, thread_ts, say, client, context,
            )
        else:
            # Action expired — update in place if possible, fall back to reply
            if channel and message_ts:
                original_blocks = body.get("message", {}).get("blocks", [])
                updated_blocks = [b for b in original_blocks if b.get("type") != "actions"]
                updated_blocks.append({
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": "⚠️  This action has already been handled or expired."},
                    ],
                })
                try:
                    await client.chat_update(
                        channel=channel,
                        ts=message_ts,
                        blocks=updated_blocks,
                        text="This action has expired.",
                    )
                except Exception as _upd_err:
                    logger.warning("hitl_expired_message_update_failed", error=str(_upd_err))
                    from lucy.pipeline.humanize import pick
                    await say(text=pick("hitl_expired"), thread_ts=thread_ts)
            else:
                from lucy.pipeline.humanize import pick
                await say(text=pick("hitl_expired"), thread_ts=thread_ts)

    @app.action(re.compile(r"lucy_action_cancel_.*"))
    async def handle_cancel_action(
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
        message_ts = body.get("message", {}).get("ts")
        thread_ts = body.get("message", {}).get("thread_ts") or message_ts

        logger.info("hitl_cancelled", action_id=action_id, user=user_name)

        from lucy.slack.hitl import resolve_pending_action
        await resolve_pending_action(action_id, approved=False)

        # Update the approval message in place: remove buttons, show rejection status.
        if channel and message_ts:
            original_blocks = body.get("message", {}).get("blocks", [])
            updated_blocks = [b for b in original_blocks if b.get("type") != "actions"]
            updated_blocks.append({
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"✕  Rejected by *{user_name}*."},
                ],
            })
            try:
                await client.chat_update(
                    channel=channel,
                    ts=message_ts,
                    blocks=updated_blocks,
                    text="Action rejected.",
                )
            except Exception as _upd_err:
                logger.warning("hitl_cancel_message_update_failed", error=str(_upd_err))
                from lucy.pipeline.humanize import pick
                await say(text=pick("hitl_cancelled", user=user_name), thread_ts=thread_ts)
        else:
            from lucy.pipeline.humanize import pick
            await say(text=pick("hitl_cancelled", user=user_name), thread_ts=thread_ts)

    # ═══ PROACTIVE EVENT CAPTURE ════════════════════════════════════════

    @app.event("reaction_added")
    async def handle_reaction_added(
        event: dict[str, Any],
        context: AsyncBoltContext,
    ) -> None:
        """Log reactions to the proactive event queue for heartbeat awareness.

        We capture: reactions on Lucy's own messages (user engagement signal)
        and high-energy emojis (tada, rocket, fire = celebration/win).
        This avoids triggering expensive agent runs per reaction.
        """
        workspace_id = str(context.get("workspace_id") or context.get("team_id") or "")
        if not workspace_id:
            return

        emoji = event.get("reaction", "")
        user = event.get("user", "")
        item = event.get("item", {})
        channel = item.get("channel", "")
        message_ts = item.get("ts", "")
        item_user = event.get("item_user", "")  # who owns the message being reacted to

        # Only log celebration/high-signal emojis OR reactions to Lucy's messages
        celebration_emojis = {
            "tada", "rocket", "fire", "100", "raised_hands",
            "clap", "muscle", "star", "sparkles", "trophy",
            "medal", "first_place_medal", "party_popper",
        }
        is_celebration = emoji in celebration_emojis

        # We don't track Lucy's bot user ID centrally -- skip the "on Lucy's message"
        # check here. The heartbeat can determine this from context if needed.
        is_on_lucy_message = False

        if not is_celebration and not is_on_lucy_message:
            return

        try:
            from lucy.workspace.filesystem import get_workspace
            from lucy.workspace.proactive_events import append_proactive_event
            ws = get_workspace(workspace_id)
            await append_proactive_event(ws, "reaction_added", {
                "emoji": emoji,
                "user": user,
                "channel": channel,
                "message_ts": message_ts,
                "is_celebration": is_celebration,
                "on_lucy_message": is_on_lucy_message,
            })
        except Exception as e:
            logger.debug("proactive_event_log_failed", event_type="reaction_added", error=str(e))

    @app.event("member_joined_channel")
    async def handle_member_joined_channel(
        event: dict[str, Any],
        context: AsyncBoltContext,
    ) -> None:
        """Log new channel members for heartbeat awareness.

        The heartbeat can use this to introduce Lucy to the new member
        or welcome them to the channel.
        """
        workspace_id = str(context.get("workspace_id") or context.get("team_id") or "")
        if not workspace_id:
            return

        user = event.get("user", "")
        channel = event.get("channel", "")
        inviter = event.get("inviter", "")

        logger.debug(
            "member_joined_channel",
            workspace_id=workspace_id,
            user=user,
            channel=channel,
        )

        try:
            from lucy.workspace.filesystem import get_workspace
            from lucy.workspace.proactive_events import append_proactive_event
            ws = get_workspace(workspace_id)
            await append_proactive_event(ws, "member_joined", {
                "user": user,
                "channel": channel,
                "inviter": inviter,
            })
        except Exception as e:
            logger.debug("proactive_event_log_failed", event_type="member_joined", error=str(e))

    @app.event("channel_created")
    async def handle_channel_created(
        event: dict[str, Any],
        context: AsyncBoltContext,
    ) -> None:
        """Log new channels for heartbeat awareness.

        The heartbeat can use this to introduce Lucy or monitor the new channel.
        """
        workspace_id = str(context.get("workspace_id") or context.get("team_id") or "")
        if not workspace_id:
            return

        channel_info = event.get("channel", {})
        channel_id = channel_info.get("id", "")
        channel_name = channel_info.get("name", "")
        creator = channel_info.get("creator", "")

        logger.debug(
            "channel_created",
            workspace_id=workspace_id,
            channel=channel_name,
        )

        try:
            from lucy.workspace.filesystem import get_workspace
            from lucy.workspace.proactive_events import append_proactive_event
            ws = get_workspace(workspace_id)
            await append_proactive_event(ws, "channel_created", {
                "channel_id": channel_id,
                "channel": channel_name,
                "creator": creator,
            })
        except Exception as e:
            logger.debug("proactive_event_log_failed", event_type="channel_created", error=str(e))

    @app.event("app_home_opened")
    async def handle_app_home_opened(
        event: dict[str, Any],
        context: AsyncBoltContext,
    ) -> None:
        """Acknowledge app home opens (required to avoid Slack warnings)."""
        pass


# ═══════════════════════════════════════════════════════════════════════
# PROGRESS UPDATE (3-MINUTE DECISION GATE)
# ═══════════════════════════════════════════════════════════════════════

def _build_progress_message(workspace_id: str) -> str | None:
    """Build a progress update from actual agent state (no LLM call).

    Reads the live tool-call counter from the running agent so the message
    reflects real work done, not generated filler.
    """
    try:
        from lucy.core.agent import get_agent
        agent = get_agent()
        tool_calls_made = getattr(agent, "_current_run_tool_count", 0)
    except Exception:
        tool_calls_made = 0

    if tool_calls_made == 0:
        return (
            "Still working on this — the initial setup took a bit longer than expected."
        )
    if tool_calls_made <= 3:
        return (
            "I've started pulling the data. The full analysis is taking a bit longer — "
            "give me a few more minutes."
        )
    return (
        f"Still working through this. I've made {tool_calls_made} API calls so far "
        f"and I'm putting the results together. Almost there."
    )


async def _maybe_send_progress(
    agent_task: asyncio.Task,  # type: ignore[type-arg]
    say: AsyncSay,
    thread_ts: str | None,
    text: str,
    workspace_id: str,
    delay_seconds: float = 180.0,
) -> None:
    """Decision gate: wait 3 minutes, then send ONE progress update if still running."""
    await asyncio.sleep(delay_seconds)
    if agent_task.done():
        return
    progress_msg = _build_progress_message(workspace_id)
    if progress_msg:
        try:
            await say(text=progress_msg, thread_ts=thread_ts)
        except Exception as exc:
            logger.debug("progress_update_failed", workspace_id=workspace_id, error=str(exc))


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
    progress_task: asyncio.Task | None = None  # type: ignore[type-arg]
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

        agent_task = asyncio.create_task(_sync_run())
        progress_task = asyncio.create_task(
            _maybe_send_progress(agent_task, say, thread_ts, text, workspace_id)
        )
        progress_task.add_done_callback(_log_task_exception)

        try:
            response_text = await asyncio.wait_for(
                asyncio.shield(agent_task), timeout=HANDLER_EXECUTION_TIMEOUT,
            )
        except asyncio.TimeoutError:
            agent_task.cancel()
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

        # The HITL approval blocks were already posted to Slack directly.
        # Returning this sentinel means "I already responded via blocks" —
        # don't post any additional message that would clutter the thread.
        if response_text == "__hitl_pending__":
            return

        response_text = response_text or ""
        run_meta = getattr(agent, "_last_run_metadata", {})
        skip_tone = run_meta.get("should_skip_tone_validation", False)
        slack_text = await process_output(
            response_text, skip_tone_validation=skip_tone,
        )
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
        if progress_task is not None and not progress_task.done():
            progress_task.cancel()
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
    """Run agent with strategy-driven recovery on exception.

    Uses the error_strategy engine to classify each failure and pick a
    concrete recovery action (wait time, model override, failure context,
    tool pruning). Retries up to 3 times with an escalating strategy
    before delivering an actionable degradation message.
    """
    from lucy.core.openclaw import OpenClawError
    from lucy.pipeline.error_strategy import (
        classify_error,
        get_actionable_degradation_message,
        get_recovery_strategy,
        should_give_up,
    )

    last_exc: Exception | None = None
    classification = None

    for attempt in range(4):  # attempt 0 = first try, 1-3 = recovery attempts
        try:
            if attempt == 0:
                return await agent.run(message=text, ctx=ctx, slack_client=slack_client)

            # Use the strategy engine for all retry attempts
            strategy = get_recovery_strategy(classification, attempt - 1)
            logger.info(
                "recovery_attempt",
                workspace_id=workspace_id,
                attempt=attempt,
                strategy=strategy.name,
                wait_seconds=strategy.wait_seconds,
                model_override=strategy.model_override,
            )
            if strategy.wait_seconds > 0:
                await asyncio.sleep(strategy.wait_seconds)

            run_kwargs: dict[str, Any] = {
                "message": text,
                "ctx": ctx,
                "slack_client": slack_client,
                "failure_context": strategy.failure_context,
            }
            if strategy.model_override:
                run_kwargs["model_override"] = strategy.model_override

            return await agent.run(**run_kwargs)

        except OpenClawError as e:
            last_exc = e
            classification = classify_error(e)
            logger.warning(
                "agent_run_failed",
                workspace_id=workspace_id,
                attempt=attempt,
                status_code=e.status_code,
                category=classification.category,
            )
            if classification.is_client_fault and attempt == 0:
                raise

        except Exception as e:
            last_exc = e
            classification = classify_error(e)
            logger.warning(
                "agent_run_error",
                workspace_id=workspace_id,
                attempt=attempt,
                category=classification.category,
                error=str(e)[:200],
            )
            if classification.is_client_fault and attempt == 0:
                raise

        if classification and should_give_up(classification, attempt + 1):
            break

    degradation_msg = (
        get_actionable_degradation_message(classification)
        if classification
        else "All recovery attempts exhausted."
    )
    logger.error(
        "recovery_attempts_exhausted",
        workspace_id=workspace_id,
        category=classification.category if classification else "unknown",
    )
    raise RuntimeError(degradation_msg)


# ═══════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════

def _log_task_exception(task: asyncio.Task) -> None:  # type: ignore[type-arg]
    if not task.cancelled() and task.exception():
        logger.warning("background_task_failed", error=str(task.exception()))


def _clean_mention(text: str) -> str:
    """Remove @Lucy mention and decode Slack formatting in message text.

    Converts Slack's mrkdwn encoding to plain text so regex-based
    tools (memory extraction, intent detection) work correctly:
      <mailto:a@b.com|a@b.com>  →  a@b.com
      <https://example.com|label>  →  label
      <#C123|channel-name>  →  #channel-name
      <@U123>  →  (removed)
    """
    # Strip @Lucy mention
    text = re.sub(r"<@[A-Z0-9]+>", "", text)
    # Decode mailto: links — keep the email address part
    text = re.sub(r"<mailto:([^|>]+)\|[^>]*>", r"\1", text)
    text = re.sub(r"<mailto:([^>]+)>", r"\1", text)
    # Decode hyperlinks — prefer the display label when present
    text = re.sub(r"<https?://[^|>]+\|([^>]+)>", r"\1", text)
    text = re.sub(r"<https?://([^>]+)>", r"https://\1", text)
    # Decode channel references
    text = re.sub(r"<#[A-Z0-9]+\|([^>]+)>", r"#\1", text)
    return text.strip()


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
    parameters = action_data.get("parameters", {})
    try:
        from lucy.core.agent import AgentContext, get_agent

        agent = get_agent()
        ctx = AgentContext(
            workspace_id=workspace_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            # Only pre-approve the specific tool the user clicked Approve on.
            # Any additional WRITE/DESTRUCTIVE follow-up tool calls remain gated.
            approved_tool_name=tool_name,
        )
        # Serialize parameters as JSON (not Python repr) so the agent receives
        # a deterministic, safely-parseable format regardless of value complexity.
        import json as _json
        params_json = _json.dumps(parameters, ensure_ascii=False, indent=2)
        instruction = (
            f"The user has approved the following action. Execute it immediately "
            f"using the tool `{tool_name}` with these exact parameters — "
            f"do NOT ask for confirmation, it has already been approved:\n"
            f"{action_data.get('description', '')}\n"
            f"Tool: {tool_name}\n"
            f"Parameters (JSON):\n```json\n{params_json}\n```\n\n"
            f"IMPORTANT: If the tool fails, follow the `next_step` in the tool "
            f"result exactly. Do NOT try alternative integration methods, do NOT "
            f"suggest API keys or custom wrappers unless the tool result explicitly "
            f"says to do so. Report the failure clearly and follow the instructions "
            f"in `next_step`."
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
