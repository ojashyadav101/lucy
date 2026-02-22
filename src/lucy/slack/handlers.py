"""Slack event handlers for Lucy.

Handles:
- App mentions (@Lucy)
- Direct messages
- Slash commands (/lucy)
- Block Kit actions
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from slack_bolt.async_app import AsyncApp, AsyncAck, AsyncSay, AsyncBoltContext
from slack_bolt.request.async_request import AsyncBoltRequest

from lucy.db.models import Task, TaskStatus, TaskPriority
from lucy.db.session import AsyncSessionLocal
from lucy.slack.blocks import LucyMessage
import structlog

logger = structlog.get_logger()

# Temporary UX toggle: keep timing telemetry in logs only, not in user-visible replies.
INCLUDE_TIMING_FOOTER_IN_SLACK_REPLY = False


def register_handlers(app: AsyncApp) -> None:
    """Register all Slack event handlers with the Bolt app."""
    
    # ═════════════════════════════════════════════════════════════════════════
    # APP MENTION (@Lucy)
    # ═════════════════════════════════════════════════════════════════════════
    
    @app.event("app_mention")
    async def handle_app_mention(
        event: dict[str, Any],
        say: AsyncSay,
        context: AsyncBoltContext,
        client: Any,
        request: AsyncBoltRequest,
    ) -> None:
        """Handle @Lucy mentions in channels with micro-timing."""
        import time as _time
        from datetime import datetime, timezone
        t_event_received = _time.monotonic()
        wall_event_received = datetime.now(timezone.utc)

        text = event.get("text", "")
        channel_id = event.get("channel")
        thread_ts = event.get("thread_ts") or event.get("ts")
        user_slack_id = event.get("user")
        event_ts = event.get("ts")

        # Slack event_ts is a Unix timestamp — gives us the exact time the message was sent
        slack_msg_sent_utc = None
        if event_ts:
            try:
                slack_msg_sent_utc = datetime.fromtimestamp(float(event_ts), tz=timezone.utc)
            except (ValueError, TypeError):
                pass

        slack_to_handler_ms = None
        if slack_msg_sent_utc:
            slack_to_handler_ms = (wall_event_received - slack_msg_sent_utc).total_seconds() * 1000

        logger.info(
            "app_mention received",
            text=text[:100],
            channel=channel_id,
            user=user_slack_id,
            workspace_id=str(context.get("workspace_id", "unknown")),
            event_ts=event_ts,
            slack_msg_sent=slack_msg_sent_utc.isoformat() if slack_msg_sent_utc else None,
            handler_received=wall_event_received.isoformat(),
            slack_delivery_ms=round(slack_to_handler_ms, 1) if slack_to_handler_ms else None,
        )

        # Clean the text (remove @Lucy mention)
        clean_text = _clean_mention(text)

        if not clean_text.strip():
            await say(blocks=LucyMessage.help(), thread_ts=thread_ts)
            return

        await _create_task_and_respond(
            context=context,
            say=say,
            text=clean_text,
            channel_id=channel_id,
            thread_ts=thread_ts,
            source="app_mention",
            event_ts=event_ts,
            client=client,
            t_event_received=t_event_received,
            wall_timestamps={
                "slack_msg_sent": slack_msg_sent_utc.isoformat() if slack_msg_sent_utc else None,
                "handler_received": wall_event_received.isoformat(),
                "slack_delivery_ms": round(slack_to_handler_ms, 1) if slack_to_handler_ms else None,
            },
        )
    
    # ═════════════════════════════════════════════════════════════════════════
    # DIRECT MESSAGE & THREAD MESSAGES
    # ═════════════════════════════════════════════════════════════════════════
    
    @app.event("message")
    async def handle_message(
        event: dict[str, Any],
        say: AsyncSay,
        context: AsyncBoltContext,
        client: Any,
    ) -> None:
        """Handle DMs and smart thread responses.
        
        For DMs (channel_type="im"): Always respond.
        
        For channel messages:
        - If it's a thread reply in an active Lucy conversation: 
          Use smart classification to determine if we should respond.
        - Otherwise: Ignore (must use @mention to start a conversation).
        """
        # Skip Lucy's own messages and non-user mutation events.
        # NOTE: some user-token posts can include bot_id, so don't filter on bot_id alone.
        subtype = event.get("subtype")
        bot_user_id = context.get("bot_user_id")
        if event.get("user") == bot_user_id:
            return
        if subtype in {"message_changed", "message_deleted", "channel_join", "channel_leave"}:
            return
        
        channel_type = event.get("channel_type")
        text = event.get("text", "")
        channel_id = event.get("channel")
        thread_ts = event.get("thread_ts") or event.get("ts")
        user_slack_id = event.get("user")
        event_ts = event.get("ts")
        
        if not text.strip():
            return
        
        # DM handling (im = instant message/direct message)
        if channel_type == "im":
            logger.info(
                "direct_message received",
                text=text[:100],
                workspace_id=str(context.get("workspace_id", "unknown")),
            )
            await _create_task_and_respond(
                context=context,
                say=say,
                text=text,
                channel_id=channel_id,
                thread_ts=thread_ts,
                source="direct_message",
                event_ts=event_ts,
                client=client,
            )
            return
        
        # Channel message handling
        # Only process if it's in a thread (not a top-level message)
        # and not an @mention (handled by app_mention handler)
        if not event.get("thread_ts"):
            # Top-level channel message without @mention - ignore
            return
        
        # Check if this is part of an active Lucy conversation
        from lucy.slack.thread_manager import get_thread_manager
        thread_manager = get_thread_manager()
        
        workspace_id = context.get("workspace_id")
        channel_uuid = context.get("channel_id")  # This is the UUID, not Slack ID
        
        if not workspace_id or not channel_uuid:
            logger.debug("missing_workspace_or_channel_id")
            return
        
        # Use smart classification to determine if we should respond
        should_respond, classification = await thread_manager.should_respond_to_message(
            workspace_id=workspace_id,
            channel_id=channel_uuid,
            thread_ts=event.get("thread_ts"),
            user_slack_id=user_slack_id,
            message_text=text,
            message_ts=event_ts,
        )
        
        logger.info(
            "thread_message_classified",
            text=text[:100],
            should_respond=should_respond,
            classification=classification.get("classification"),
            reason=classification.get("reason"),
            confidence=classification.get("confidence"),
            workspace_id=str(workspace_id),
        )
        
        if should_respond:
            await _create_task_and_respond(
                context=context,
                say=say,
                text=text,
                channel_id=channel_id,
                thread_ts=thread_ts,
                source="thread_smart_response",
                event_ts=event_ts,
                client=client,
            )
    
    # ═════════════════════════════════════════════════════════════════════════
    # SLASH COMMANDS
    # ═════════════════════════════════════════════════════════════════════════
    
    @app.command("/lucy")
    async def handle_slash_command(
        ack: AsyncAck,
        command: dict[str, Any],
        say: AsyncSay,
        context: AsyncBoltContext,
    ) -> None:
        """Handle /lucy slash command."""
        await ack()  # Must acknowledge within 3 seconds
        
        text = command.get("text", "").strip().lower()
        
        logger.info(
            "slash_command received",
            command="/lucy",
            args=text,
            workspace_id=str(context.get("workspace_id", "unknown")),
        )
        
        if not text or text == "help":
            await say(blocks=LucyMessage.help())
        elif text == "status":
            await say(blocks=LucyMessage.status())
        elif text == "sync":
            workspace_id = context.get("workspace_id")
            if workspace_id:
                try:
                    from lucy.integrations.registry import get_integration_registry
                    registry = get_integration_registry()
                    await registry.sync_workspace_connections(workspace_id)
                    providers = await registry.get_active_providers(workspace_id)
                    await say(blocks=LucyMessage.simple_response(f"Sync complete. Active connections: {', '.join(providers) if providers else 'None'}", emoji="🔄"))
                except Exception as e:
                    logger.error("sync_failed", error=str(e))
                    await say(blocks=LucyMessage.error("Failed to sync connections."))
        elif text.startswith("echo "):
            # Echo command for testing
            message = text[5:]  # Remove "echo "
            await say(
                blocks=LucyMessage.simple_response(f"Echo: {message}", emoji="📢"),
            )
        elif text.startswith("connect "):
            provider = text[8:].strip()
            if not provider:
                await say(blocks=LucyMessage.error("Please specify a tool to connect, e.g., `/lucy connect github`"))
            else:
                await _handle_connect_command(context, say, provider)
        else:
            # Treat as a task request
            await _create_task_and_respond(
                context=context,
                say=say,
                text=text,
                channel_id=command.get("channel_id"),
                thread_ts=None,
                source="slash_command",
                event_ts=command.get("trigger_id"),
            )
    
    # ═════════════════════════════════════════════════════════════════════════
    # BLOCK KIT ACTIONS
    # ═════════════════════════════════════════════════════════════════════════
    
    @app.action(r"lucy_action_.*")
    async def handle_block_action(
        ack: AsyncAck,
        body: dict[str, Any],
        say: AsyncSay,
        context: AsyncBoltContext,
    ) -> None:
        """Handle Block Kit button clicks."""
        await ack()
        
        action = body.get("actions", [{}])[0]
        action_id = action.get("action_id", "")
        value = action.get("value", "")
        
        logger.info(
            "block_action received",
            action_id=action_id,
            value=value,
            workspace_id=str(context.get("workspace_id", "unknown")),
        )
        
        # Parse action type
        if action_id.startswith("lucy_action_approve:"):
            await _handle_approval_action(
                context=context,
                say=say,
                approval_id=value,
                approved=True,
            )
        elif action_id.startswith("lucy_action_reject:"):
            await _handle_approval_action(
                context=context,
                say=say,
                approval_id=value,
                approved=False,
            )
        elif action_id.startswith("lucy_action_view_task:"):
            await say(
                blocks=LucyMessage.simple_response(
                    f"Task details: {value}",
                    emoji="📋",
                ),
            )
        elif action_id.startswith("lucy_action_view_approval:"):
            await say(
                blocks=LucyMessage.simple_response(
                    f"Approval details: {value}",
                    emoji="📋",
                ),
            )
        else:
            await say(
                blocks=LucyMessage.simple_response(
                    f"Action received: {action_id}",
                    emoji="✅",
                ),
            )


# ═════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═════════════════════════════════════════════════════════════════════════════

def _clean_mention(text: str) -> str:
    """Remove @Lucy mention from text."""
    import re
    # Remove Slack user mentions (<@USER_ID>)
    cleaned = re.sub(r"<@[A-Z0-9]+>", "", text)
    return cleaned.strip()


def _to_slack_mrkdwn(text: str) -> str:
    """Convert common Markdown patterns to Slack mrkdwn."""
    import re

    converted = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    converted = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"<\2|\1>", converted)
    return converted


async def _create_task_and_respond(
    context: AsyncBoltContext,
    say: Any,
    text: str,
    channel_id: str | None,
    thread_ts: str | None,
    source: str,
    event_ts: str | None = None,
    client: Any = None,
    t_event_received: float | None = None,
    wall_timestamps: dict[str, Any] | None = None,
) -> None:
    """Create a task record and send acknowledgment with micro-timing."""
    import time as _time
    workspace_id: uuid.UUID | None = context.get("workspace_id")
    user_id: uuid.UUID | None = context.get("user_id")

    if not workspace_id:
        logger.error("No workspace_id in context")
        await say(blocks=LucyMessage.error("Unable to process request: workspace not found.", error_code="NO_WORKSPACE"))
        return

    t_start = _time.monotonic()
    timing: dict[str, float] = {}

    # Get channel UUID from context (set by middleware)
    channel_uuid: uuid.UUID | None = context.get("channel_id")
    
    async with AsyncSessionLocal() as db:
        # Deduplicate using event_ts
        t_dup_start = _time.monotonic()
        if event_ts:
            from sqlalchemy import select
            result = await db.execute(
                select(Task).where(
                    Task.workspace_id == workspace_id,
                    Task.config["event_ts"].astext == event_ts
                )
            )
            if result.scalar_one_or_none():
                logger.info("duplicate_event_ignored", event_ts=event_ts)
                return
        timing["dedup_check_ms"] = (_time.monotonic() - t_dup_start) * 1000

        # Create task
        t_task_start = _time.monotonic()
        
        # Get slack_user_id from context (set by middleware)
        slack_user_id = context.get("slack_user_id") if context and hasattr(context, 'get') else None
        
        task = Task(
            workspace_id=workspace_id,
            requester_id=user_id,
            channel_id=channel_uuid,  # Set the DB channel UUID for thread tracking
            intent="chat",
            priority=TaskPriority.NORMAL,
            status=TaskStatus.CREATED,
            config={
                "source": source,
                "channel_id": str(channel_id) if channel_id else None,
                "thread_ts": str(thread_ts) if thread_ts else None,
                "original_text": str(text) if text else None,
                "event_ts": str(event_ts) if event_ts else None,
                "slack_user_id": str(slack_user_id) if slack_user_id else None,
                "slack_channel_id": str(channel_id) if channel_id else None,
            },
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)
        timing["task_create_ms"] = (_time.monotonic() - t_task_start) * 1000

        t_react_start = _time.monotonic()

        async def _add_reaction():
            if client and channel_id and event_ts:
                try:
                    await client.reactions_add(
                        channel=channel_id,
                        name="hourglass_flowing_sand",
                        timestamp=event_ts,
                    )
                except Exception as e:
                    logger.warning("reaction_add_failed", error=str(e))

        asyncio.create_task(_add_reaction())
        timing["reaction_add_ms"] = (_time.monotonic() - t_react_start) * 1000

        # Calculate total time from event received to reaction
        if t_event_received:
            timing["total_ack_ms"] = (_time.monotonic() - t_event_received) * 1000

        # Store timing in task config for later analysis
        task.config["timing_ack"] = timing
        await db.commit()

        from datetime import datetime as _dt, timezone as _tz
        wall_ack_done = _dt.now(_tz.utc).isoformat()

        logger.info(
            "acknowledgment_complete",
            task_id=str(task.id),
            dedup_ms=round(timing.get("dedup_check_ms", 0), 1),
            task_create_ms=round(timing.get("task_create_ms", 0), 1),
            reaction_ms=round(timing.get("reaction_add_ms", 0), 1),
            total_ack_ms=round(timing.get("total_ack_ms", 0), 1),
            wall_ack_done=wall_ack_done,
            slack_delivery_ms=(wall_timestamps or {}).get("slack_delivery_ms"),
        )

        asyncio.create_task(
            _execute_and_respond(
                task_id=task.id,
                say=say,
                thread_ts=thread_ts,
                client=client,
                channel_id=channel_id,
                event_ts=event_ts,
                timing_ack=timing,
                wall_timestamps={
                    **(wall_timestamps or {}),
                    "ack_done": wall_ack_done,
                },
            )
        )


async def _execute_and_respond(
    task_id: uuid.UUID,
    say: Any,
    thread_ts: str | None,
    client: Any = None,
    channel_id: str | None = None,
    event_ts: str | None = None,
    timing_ack: dict[str, float] | None = None,
    wall_timestamps: dict[str, Any] | None = None,
) -> None:
    """Execute task via agent and send result to Slack with full micro-timing."""
    import time
    from datetime import datetime as _dt, timezone as _tz
    from lucy.core.agent import execute_task
    from lucy.db.session import AsyncSessionLocal
    from sqlalchemy import select

    t_start = time.monotonic()

    try:
        final_status = await execute_task(task_id)
        t_after_execute = time.monotonic()

        async with AsyncSessionLocal() as db:
            from lucy.db.models import Task

            result = await db.execute(select(Task).where(Task.id == task_id))
            task = result.scalar_one()

            t_after_db = time.monotonic()

            if final_status == TaskStatus.COMPLETED and task.result_data:
                full_response = task.result_data.get("full_response", "Task completed.")
                exec_ms = task.result_data.get("elapsed_ms", 0)

                # Full timing breakdown
                total_exec_ms = int((t_after_db - t_start) * 1000)
                slack_overhead_ms = int((t_after_db - t_after_execute) * 1000)

                # Ack timing from earlier phase
                ack_timing = timing_ack or {}
                dedup_ms = ack_timing.get("dedup_check_ms", 0)
                task_create_ms = ack_timing.get("task_create_ms", 0)
                reaction_ms = ack_timing.get("reaction_add_ms", 0)
                total_ack_ms = ack_timing.get("total_ack_ms", 0)

                tier = task.model_tier or "UNKNOWN"
                model_name = task.result_data.get("model", tier)
                msg_count = task.result_data.get("message_count", 1)

                wall = wall_timestamps or {}
                wall_response_sent = _dt.now(_tz.utc).isoformat()
                slack_delivery = wall.get("slack_delivery_ms")

                delivery_line = (
                    f"• Slack → Lucy delivery: {slack_delivery:.0f}ms\n"
                    if slack_delivery else ""
                )

                timing_footer = (
                    f"\n\n⏱️ *Full Timing Breakdown:*\n"
                    f"{delivery_line}"
                    f"• Acknowledgment (event → ⏳ emoji): {total_ack_ms:.0f}ms\n"
                    f"  └─ Deduplication check: {dedup_ms:.0f}ms\n"
                    f"  └─ Task creation: {task_create_ms:.0f}ms\n"
                    f"  └─ Reaction add: {reaction_ms:.0f}ms\n"
                    f"• LLM execution: {exec_ms}ms\n"
                    f"• Slack final response: {slack_overhead_ms}ms\n"
                    f"• Total from event: {total_ack_ms + total_exec_ms:.0f}ms\n\n"
                    f"*Model:* {model_name} | *Intent:* {task.intent or 'unknown'} | *Thread msgs:* {msg_count}\n\n"
                    f"_Timestamps (UTC):_\n"
                    f"  Message sent: `{wall.get('slack_msg_sent', '?')}`\n"
                    f"  Lucy received: `{wall.get('handler_received', '?')}`\n"
                    f"  Ack done: `{wall.get('ack_done', '?')}`\n"
                    f"  Response sent: `{wall_response_sent}`"
                )

                response_text = (
                    full_response + timing_footer
                    if INCLUDE_TIMING_FOOTER_IN_SLACK_REPLY
                    else full_response
                )

                logger.info(
                    "sending_llm_result_to_slack",
                    task_id=str(task_id),
                    response_len=len(full_response),
                    exec_ms=exec_ms,
                    total_ack_ms=total_ack_ms,
                    total_exec_ms=total_exec_ms,
                    thread_ts=thread_ts,
                )
                await say(text=_to_slack_mrkdwn(response_text), thread_ts=thread_ts)
                logger.info("llm_result_sent_to_slack", task_id=str(task_id))
                
                # Record that Lucy responded in this thread
                # This enables smart auto-response for follow-up messages
                if thread_ts and channel_id and task.channel_id:
                    try:
                        from lucy.slack.thread_manager import get_thread_manager
                        thread_manager = get_thread_manager()
                        await thread_manager.record_lucy_response(
                            workspace_id=task.workspace_id,
                            channel_id=task.channel_id,
                            thread_ts=thread_ts,
                            user_id=task.requester_id,
                            slack_channel_id=channel_id,
                            slack_user_id=task.config.get("slack_user_id") if task.config else None,
                            task_id=task_id,
                            intent=task.intent,
                        )
                    except Exception as e:
                        logger.warning("thread_tracking_failed", error=str(e), task_id=str(task_id))
            else:
                logger.warning(
                    "task_not_completed_or_no_result",
                    task_id=str(task_id),
                    final_status=str(final_status),
                    has_result_data=task.result_data is not None,
                )
                # Send error
                await say(
                    blocks=LucyMessage.error(
                        message=task.status_reason or "Task failed to complete.",
                        error_code="TASK_FAILED",
                        suggestion="Try rephrasing your request or contact support.",
                    ),
                    thread_ts=thread_ts,
                )
                
    except Exception as e:
        logger.error("task_execution_failed", task_id=str(task_id), error=str(e))
        await say(
            blocks=LucyMessage.error(
                message="An error occurred while processing your request.",
                error_code="EXECUTION_ERROR",
                suggestion="Please try again in a moment.",
            ),
            thread_ts=thread_ts,
        )
    finally:
        if client and channel_id and event_ts:
            try:
                await client.reactions_remove(
                    channel=channel_id,
                    name="hourglass_flowing_sand",
                    timestamp=event_ts,
                )
            except Exception as e:
                logger.debug("reaction_remove_failed", error=str(e))


async def _handle_approval_action(
    context: AsyncBoltContext,
    say: Any,
    approval_id: str,
    approved: bool,
) -> None:
    """Handle approval/rejection button clicks."""
    from lucy.db.models import Approval, ApprovalStatus
    
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Approval).where(Approval.id == uuid.UUID(approval_id))
        )
        approval = result.scalar_one_or_none()
        
        if not approval:
            await say(
                blocks=LucyMessage.error(
                    "Approval request not found.",
                    suggestion="It may have expired or been already processed.",
                ),
            )
            return
        
        # Update approval
        approval.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.REJECTED
        approval.responded_at = datetime.now(timezone.utc)
        
        # Update associated task
        if approval.task_id:
            result = await db.execute(
                select(Task).where(Task.id == approval.task_id)
            )
            task = result.scalar_one_or_none()
            if task:
                if approved:
                    task.status = TaskStatus.RUNNING
                    task.queued_at = datetime.now(timezone.utc)
                else:
                    task.status = TaskStatus.CANCELLED
                    task.status_reason = "Approval rejected"
        
        await db.commit()
        
        # Confirm to user
        action_text = "approved" if approved else "rejected"
        await say(
            blocks=LucyMessage.simple_response(
                f"Request {action_text}. Lucy will proceed accordingly.",
                emoji="✅" if approved else "🚫",
            ),
        )


async def _handle_connect_command(
    context: AsyncBoltContext,
    say: Any,
    provider: str,
) -> None:
    """Handle /lucy connect <provider>."""
    workspace_id = context.get("workspace_id")
    if not workspace_id:
        return
    
    await say(blocks=LucyMessage.thinking(f"connecting to {provider}"))
    
    try:
        from lucy.integrations.composio_client import get_composio_client
        client = get_composio_client()
        url = await client.create_connection_link(entity_id=str(workspace_id), app=provider.upper())
        
        if url:
            await say(blocks=LucyMessage.connection_request(provider_name=provider, oauth_url=url))
        else:
            await say(blocks=LucyMessage.error(f"Could not generate connection link for {provider}."))
    except Exception as e:
        logger.error("connect_command_failed", provider=provider, error=str(e))
        await say(blocks=LucyMessage.error(f"Failed to connect {provider}. Is it a supported tool?"))


# Import select for the helper functions
from sqlalchemy import select
