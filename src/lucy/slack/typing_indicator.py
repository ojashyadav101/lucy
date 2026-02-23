"""Typing indicator for Lucy's Slack messages.

Sends periodic typing indicators while the agent is processing,
so users see "Lucy is typing..." in the Slack UI.

Usage:
    async with TypingIndicator(client, channel_id):
        response = await agent.run(...)

Currently a no-op placeholder (Slack bot API doesn't support typing
natively) but the abstraction is in place for when it does.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog

logger = structlog.get_logger()

TYPING_INTERVAL_SECONDS = 3.0


class TypingIndicator:
    """Context manager that sends periodic typing indicators."""

    def __init__(self, client: Any, channel_id: str | None) -> None:
        self._client = client
        self._channel_id = channel_id
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> "TypingIndicator":
        if self._client and self._channel_id:
            self._task = asyncio.create_task(self._loop())
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        """Send typing indicator every TYPING_INTERVAL_SECONDS."""
        try:
            while True:
                try:
                    pass
                except Exception:
                    pass
                await asyncio.sleep(TYPING_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            pass
