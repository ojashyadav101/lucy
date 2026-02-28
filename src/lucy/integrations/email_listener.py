"""WebSocket email listener — real-time inbound email processing.

Maintains a persistent WebSocket connection to AgentMail, subscribes
to Lucy's inbox(es), and routes inbound emails to Slack as notifications.

Lifecycle matches the cron scheduler: started in ``app.py`` lifespan,
runs as a background ``asyncio.Task``, stops on shutdown.
"""

from __future__ import annotations

import asyncio
import math
from typing import Any

import structlog

from lucy.config import settings

logger = structlog.get_logger()

_listener: EmailListener | None = None

_MAX_BACKOFF_SECONDS = 120
_BASE_BACKOFF_SECONDS = 2


class EmailListener:
    """Persistent WebSocket listener for inbound emails."""

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._consecutive_failures = 0

    @property
    def running(self) -> bool:
        return self._running

    async def start(
        self,
        slack_client: Any,
        inbox_ids: list[str],
        notification_channel: str | None = None,
    ) -> None:
        """Start the listener as a background task."""
        if self._running:
            logger.warning("email_listener_already_running")
            return

        self._running = True
        self._task = asyncio.create_task(
            self._listen_loop(slack_client, inbox_ids, notification_channel),
        )
        logger.info(
            "email_listener_started",
            inbox_ids=inbox_ids,
        )

    async def stop(self) -> None:
        """Gracefully stop the listener."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("email_listener_stopped")

    async def _listen_loop(
        self,
        slack_client: Any,
        inbox_ids: list[str],
        notification_channel: str | None,
    ) -> None:
        """Connect → subscribe → process events.  Auto-reconnect on failure."""
        from agentmail import (
            AsyncAgentMail,
            MessageReceivedEvent,
            Subscribe,
            Subscribed,
        )

        client = AsyncAgentMail(api_key=settings.agentmail_api_key)

        while self._running:
            try:
                async with client.websockets.connect() as socket:
                    await socket.send_subscribe(
                        Subscribe(inbox_ids=inbox_ids),
                    )

                    self._consecutive_failures = 0
                    logger.info("email_ws_connected", inbox_ids=inbox_ids)

                    async for event in socket:
                        if not self._running:
                            break

                        if isinstance(event, Subscribed):
                            logger.info(
                                "email_ws_subscribed",
                                inbox_ids=getattr(event, "inbox_ids", []),
                            )
                        elif isinstance(event, MessageReceivedEvent):
                            await self._handle_inbound(
                                event,
                                slack_client,
                                notification_channel,
                            )

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._consecutive_failures += 1
                backoff = min(
                    _BASE_BACKOFF_SECONDS * math.pow(2, self._consecutive_failures - 1),
                    _MAX_BACKOFF_SECONDS,
                )
                logger.error(
                    "email_ws_error",
                    error=str(e),
                    consecutive_failures=self._consecutive_failures,
                    backoff_seconds=backoff,
                )
                await asyncio.sleep(backoff)

    async def _handle_inbound(
        self,
        event: Any,
        slack_client: Any,
        notification_channel: str | None,
    ) -> None:
        """Process an inbound email: post a Slack notification."""
        msg = event.message
        from_ = getattr(msg, "from_", "unknown sender")
        subject = getattr(msg, "subject", "(no subject)") or "(no subject)"
        text_preview = (getattr(msg, "text", "") or "")[:500]
        inbox_id = getattr(msg, "inbox_id", None)
        message_id = getattr(msg, "message_id", None)
        thread_id = getattr(msg, "thread_id", None)

        logger.info(
            "email_received",
            from_=from_,
            subject=subject,
            inbox_id=inbox_id,
            message_id=message_id,
        )

        if not slack_client or not notification_channel:
            return

        try:
            blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"*New email received*\n"
                            f"*From:* {from_}\n"
                            f"*Subject:* {subject}"
                        ),
                    },
                },
            ]
            if text_preview:
                blocks.append({
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"```{text_preview}```",
                    },
                })

            context_parts = []
            if thread_id:
                context_parts.append(f"Thread: {thread_id}")
            if message_id:
                context_parts.append(f"Message: {message_id}")
            if context_parts:
                blocks.append({
                    "type": "context",
                    "elements": [
                        {"type": "mrkdwn", "text": " | ".join(context_parts)},
                    ],
                })

            await slack_client.chat_postMessage(
                channel=notification_channel,
                text=f"New email from {from_}: {subject}",
                blocks=blocks,
            )

        except Exception as e:
            logger.error(
                "email_slack_notification_failed",
                error=str(e),
                from_=from_,
                subject=subject,
            )


# ── Singleton access ─────────────────────────────────────────────────


def get_email_listener() -> EmailListener:
    """Return the singleton email listener."""
    global _listener
    if _listener is None:
        _listener = EmailListener()
    return _listener
