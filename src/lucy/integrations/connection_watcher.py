"""Background watcher for pending OAuth connections.

When Lucy sends an auth URL to the user, this module polls Composio until
the connection becomes ACTIVE, then proactively notifies the user in-thread
and re-triggers the original task.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger()

POLL_INTERVAL_SECONDS = 5
MAX_POLL_DURATION_SECONDS = 600
MAX_CONCURRENT_WATCHES = 20


@dataclass
class PendingConnection:
    """A connection that Lucy is waiting for the user to complete."""

    workspace_id: str
    toolkit_slug: str
    display_name: str
    channel_id: str
    thread_ts: str
    original_request: str
    entity_ids: list[str] = field(default_factory=list)


_active_watches: dict[str, asyncio.Task[None]] = {}
_background_tasks: set[asyncio.Task[None]] = set()
_resume_locks: dict[str, asyncio.Lock] = {}
_resumed_threads: set[str] = set()


def start_watching(
    pending: PendingConnection,
    say_fn: Any,
    slack_client: Any | None = None,
) -> bool:
    """Start a background poller for a pending OAuth connection.

    This is a sync function that creates an asyncio task. The task is kept
    alive via a strong reference in _background_tasks.

    Returns True if a watcher was started.
    """
    watch_key = f"{pending.workspace_id}:{pending.toolkit_slug}:{pending.thread_ts}"

    if watch_key in _active_watches:
        logger.debug(
            "connection_watch_already_active",
            toolkit=pending.toolkit_slug,
            thread_ts=pending.thread_ts,
        )
        return False

    if len(_active_watches) >= MAX_CONCURRENT_WATCHES:
        logger.warning("connection_watch_limit_reached")
        return False

    task = asyncio.create_task(
        _poll_until_connected(pending, say_fn, slack_client, watch_key),
        name=f"conn_watch:{pending.toolkit_slug}",
    )
    _active_watches[watch_key] = task
    _background_tasks.add(task)

    def _on_done(t: asyncio.Task[None]) -> None:
        _active_watches.pop(watch_key, None)
        _background_tasks.discard(t)
        if t.cancelled():
            logger.debug("connection_watch_cancelled", key=watch_key)
        elif t.exception():
            logger.warning(
                "connection_watch_failed",
                key=watch_key,
                error=str(t.exception()),
            )

    task.add_done_callback(_on_done)

    logger.info(
        "connection_watch_started",
        toolkit=pending.toolkit_slug,
        display=pending.display_name,
        workspace_id=pending.workspace_id,
        thread_ts=pending.thread_ts,
    )
    return True


async def _poll_until_connected(
    pending: PendingConnection,
    say_fn: Any,
    slack_client: Any | None,
    watch_key: str,
) -> None:
    """Poll Composio every few seconds until the connection becomes ACTIVE."""
    from lucy.integrations.composio_client import get_composio_client

    client = get_composio_client()
    elapsed = 0.0
    polls = 0

    logger.info(
        "connection_poll_loop_started",
        toolkit=pending.toolkit_slug,
        max_duration=MAX_POLL_DURATION_SECONDS,
        interval=POLL_INTERVAL_SECONDS,
    )

    while elapsed < MAX_POLL_DURATION_SECONDS:
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        elapsed += POLL_INTERVAL_SECONDS
        polls += 1

        try:
            is_active = await _check_connection_active(
                client, pending.toolkit_slug, pending.entity_ids,
            )
        except Exception as e:
            logger.debug(
                "connection_poll_error",
                toolkit=pending.toolkit_slug,
                poll=polls,
                error=str(e),
            )
            continue

        if polls % 12 == 0:
            logger.debug(
                "connection_poll_heartbeat",
                toolkit=pending.toolkit_slug,
                polls=polls,
                elapsed_s=elapsed,
            )

        if is_active:
            logger.info(
                "connection_activated",
                toolkit=pending.toolkit_slug,
                display=pending.display_name,
                workspace_id=pending.workspace_id,
                elapsed_s=elapsed,
                polls=polls,
            )
            await _handle_connection_success(pending, say_fn, slack_client)
            return

    logger.info(
        "connection_watch_expired",
        toolkit=pending.toolkit_slug,
        workspace_id=pending.workspace_id,
        elapsed_s=elapsed,
    )


async def _check_connection_active(
    client: Any,
    toolkit_slug: str,
    entity_ids: list[str],
) -> bool:
    """Check if a toolkit has an ACTIVE connection for any of the entity IDs."""
    composio = client._composio
    if not composio:
        return False

    def _check() -> bool:
        result = composio.connected_accounts.list(
            toolkit_slugs=[toolkit_slug],
            user_ids=entity_ids,
            statuses=["ACTIVE"],
            order_by="created_at",
            order_direction="desc",
            limit=1,
        )
        return len(result.items) > 0

    return await asyncio.to_thread(_check)


async def _handle_connection_success(
    pending: PendingConnection,
    say_fn: Any,
    slack_client: Any | None,
) -> None:
    """Send a proactive follow-up when an OAuth connection completes."""
    from lucy.integrations.composio_client import get_composio_client

    composio = get_composio_client()
    await composio.invalidate_cache(pending.workspace_id)

    others_pending = _get_sibling_watches(pending)

    if others_pending:
        names = ", ".join(others_pending)
        msg = (
            f":white_check_mark: *{pending.display_name}* is now connected! "
            f"Still waiting on: {names}."
        )
    else:
        msg = (
            f":white_check_mark: *{pending.display_name}* is now connected! "
            f"Let me pick up where I left off."
        )

    try:
        await say_fn(text=msg, thread_ts=pending.thread_ts)
    except Exception as e:
        logger.warning("connection_success_notify_failed", error=str(e))
        return

    if not others_pending:
        thread_key = f"{pending.workspace_id}:{pending.thread_ts}"
        if thread_key not in _resume_locks:
            _resume_locks[thread_key] = asyncio.Lock()

        async with _resume_locks[thread_key]:
            if thread_key in _resumed_threads:
                return
            _resumed_threads.add(thread_key)

        await _resume_original_task(pending, say_fn, slack_client)


def _get_sibling_watches(pending: PendingConnection) -> list[str]:
    """Return display names of other active watches in the same thread."""
    prefix = f"{pending.workspace_id}:"
    suffix = f":{pending.thread_ts}"
    own_key = f"{pending.workspace_id}:{pending.toolkit_slug}:{pending.thread_ts}"
    siblings: list[str] = []
    for key, task in _active_watches.items():
        if key == own_key or task.done():
            continue
        if key.startswith(prefix) and key.endswith(suffix):
            slug = key.removeprefix(prefix).removesuffix(suffix)
            from lucy.integrations.composio_client import ComposioClient
            display = ComposioClient._SLUG_DISPLAY_NAMES.get(
                slug, slug.replace("_", " ").title(),
            )
            siblings.append(display)
    return siblings


async def _resume_original_task(
    pending: PendingConnection,
    say_fn: Any,
    slack_client: Any | None,
) -> None:
    """Re-trigger the agent to continue the original task."""
    try:
        from lucy.core.agent import AgentContext, get_agent

        agent = get_agent()
        team_id = ""
        for eid in pending.entity_ids:
            if eid.startswith("slack_"):
                team_id = eid.removeprefix("slack_")
                break

        ctx = AgentContext(
            workspace_id=pending.workspace_id,
            channel_id=pending.channel_id,
            thread_ts=pending.thread_ts,
            team_id=team_id,
        )

        resume_msg = (
            f"The user just connected {pending.display_name}. "
            f"Their original request was: {pending.original_request}\n\n"
            f"Continue fulfilling their request now that {pending.display_name} "
            f"is available. Do NOT ask for authorization again."
        )

        response_text = await agent.run(
            message=resume_msg,
            ctx=ctx,
            slack_client=slack_client,
        )

        if response_text and response_text.strip():
            from lucy.pipeline.output import process_output
            from lucy.slack.blockkit import text_to_blocks
            from lucy.slack.rich_output import (
                enhance_blocks,
                format_links,
            )

            slack_text = await process_output(response_text)
            slack_text = format_links(slack_text or "")
            if slack_text:
                blocks = text_to_blocks(slack_text)
                await say_fn(
                    blocks=enhance_blocks(blocks),
                    text=slack_text[:300],
                    thread_ts=pending.thread_ts,
                )

    except Exception as e:
        logger.error(
            "connection_resume_failed",
            toolkit=pending.toolkit_slug,
            error=str(e),
        )


def get_entity_ids_for_workspace(workspace_id: str) -> list[str]:
    """Build the list of entity IDs to poll for a workspace."""
    from lucy.integrations.composio_client import get_composio_client

    client = get_composio_client()
    ids = [str(workspace_id)]
    mapped = client._entity_id_map.get(str(workspace_id))
    if mapped and mapped not in ids:
        ids.append(mapped)
    ids.append("default")
    return ids
