"""AgentMail client — Lucy's native email identity.

Async wrapper over the AgentMail SDK. Provides inbox management,
send/receive/reply, thread listing, and message search.

Singleton access via ``get_email_client()``.
"""

from __future__ import annotations

from typing import Any

import structlog

from lucy.config import settings

logger = structlog.get_logger()

_client: LucyEmailClient | None = None


class LucyEmailClient:
    """Async AgentMail client for Lucy's native email identity."""

    def __init__(self, api_key: str, domain: str = "zeeyamail.com") -> None:
        from agentmail import AsyncAgentMail

        self._client = AsyncAgentMail(api_key=api_key)
        self._domain = domain
        self._default_inbox_id = f"lucy@{domain}"

    @property
    def default_inbox(self) -> str:
        return self._default_inbox_id

    # ── Inbox Management ─────────────────────────────────────────────

    async def create_inbox(
        self,
        username: str,
        display_name: str = "Lucy",
    ) -> str:
        """Create a new inbox and return the inbox_id (email address)."""
        from agentmail.inboxes.types.create_inbox_request import (
            CreateInboxRequest,
        )

        inbox = await self._client.inboxes.create(
            request=CreateInboxRequest(
                username=username,
                domain=self._domain,
                display_name=display_name,
            ),
        )
        inbox_id = getattr(inbox, "inbox_id", None)
        logger.info(
            "inbox_created",
            inbox_id=inbox_id,
            display_name=display_name,
        )
        return inbox_id

    async def list_inboxes(self) -> list[dict[str, Any]]:
        """List all inboxes for this account."""
        result = await self._client.inboxes.list()
        inboxes = getattr(result, "inboxes", None) or []
        return [
            {
                "inbox_id": getattr(ib, "inbox_id", None),
                "display_name": getattr(ib, "display_name", None),
                "created_at": str(getattr(ib, "created_at", "")),
            }
            for ib in inboxes
        ]

    # ── Send / Reply ─────────────────────────────────────────────────

    async def send_email(
        self,
        to: list[str],
        subject: str,
        text: str,
        html: str | None = None,
        inbox_id: str | None = None,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
    ) -> dict[str, Any]:
        """Send an email from Lucy's inbox."""
        iid = inbox_id or self._default_inbox_id
        response = await self._client.inboxes.messages.send(
            inbox_id=iid,
            to=to,
            subject=subject,
            text=text,
            html=html,
            cc=cc,
            bcc=bcc,
        )
        msg_id = getattr(response, "message_id", None)
        logger.info(
            "email_sent",
            inbox_id=iid,
            to=to,
            subject=subject,
            message_id=msg_id,
        )
        return {
            "success": True,
            "message_id": msg_id,
            "from": iid,
            "to": to,
            "subject": subject,
        }

    async def reply_to_email(
        self,
        message_id: str,
        text: str,
        html: str | None = None,
        inbox_id: str | None = None,
    ) -> dict[str, Any]:
        """Reply to a specific message."""
        iid = inbox_id or self._default_inbox_id
        response = await self._client.inboxes.messages.reply(
            inbox_id=iid,
            message_id=message_id,
            text=text,
            html=html,
        )
        reply_id = getattr(response, "message_id", None)
        logger.info(
            "email_replied",
            inbox_id=iid,
            original_message_id=message_id,
            reply_message_id=reply_id,
        )
        return {
            "success": True,
            "message_id": reply_id,
            "in_reply_to": message_id,
        }

    # ── Read / List ──────────────────────────────────────────────────

    async def list_threads(
        self,
        inbox_id: str | None = None,
        labels: list[str] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List recent email threads."""
        iid = inbox_id or self._default_inbox_id
        result = await self._client.inboxes.threads.list(
            inbox_id=iid,
            labels=labels,
            limit=limit,
        )
        threads = getattr(result, "threads", None) or []
        return [_serialize_thread(t) for t in threads]

    async def get_thread(self, thread_id: str) -> dict[str, Any]:
        """Get a full thread with all messages."""
        thread = await self._client.threads.get(thread_id=thread_id)
        return _serialize_thread(thread)

    async def list_messages(
        self,
        inbox_id: str | None = None,
        limit: int = 20,
        labels: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """List recent messages in an inbox."""
        iid = inbox_id or self._default_inbox_id
        result = await self._client.inboxes.messages.list(
            inbox_id=iid,
            limit=limit,
            labels=labels,
        )
        messages = getattr(result, "messages", None) or []
        return [_serialize_message(m) for m in messages]

    # ── Search ───────────────────────────────────────────────────────

    async def search_messages(
        self,
        query: str,
        inbox_id: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Search inbox messages by keyword matching.

        Fetches recent messages and filters client-side.  A future SDK
        release may add server-side search.
        """
        iid = inbox_id or self._default_inbox_id
        result = await self._client.inboxes.messages.list(
            inbox_id=iid,
            limit=100,
        )
        messages = getattr(result, "messages", None) or []

        query_lower = query.lower()
        matches: list[dict[str, Any]] = []
        for msg in messages:
            subject = getattr(msg, "subject", "") or ""
            text = getattr(msg, "text", "") or ""
            from_ = getattr(msg, "from_", "") or ""
            searchable = f"{subject} {text} {from_}".lower()
            if query_lower in searchable:
                matches.append(_serialize_message(msg))
                if len(matches) >= limit:
                    break
        return matches


# ── Serialization helpers ────────────────────────────────────────────


def _serialize_message(msg: Any) -> dict[str, Any]:
    """Convert an SDK message object to a plain dict."""
    return {
        "message_id": getattr(msg, "message_id", None),
        "thread_id": getattr(msg, "thread_id", None),
        "from": getattr(msg, "from_", None),
        "to": getattr(msg, "to", None),
        "subject": getattr(msg, "subject", None),
        "text": _truncate(getattr(msg, "text", None), 2000),
        "date": str(getattr(msg, "created_at", "")),
    }


def _serialize_thread(thread: Any) -> dict[str, Any]:
    """Convert an SDK thread object to a plain dict."""
    messages_raw = getattr(thread, "messages", None) or []
    return {
        "thread_id": getattr(thread, "thread_id", None),
        "subject": getattr(thread, "subject", None),
        "message_count": len(messages_raw),
        "messages": [_serialize_message(m) for m in messages_raw],
        "updated_at": str(getattr(thread, "updated_at", "")),
    }


def _truncate(text: str | None, max_len: int) -> str | None:
    if text is None:
        return None
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."


# ── Singleton access ─────────────────────────────────────────────────


def get_email_client() -> LucyEmailClient:
    """Return the singleton email client.

    Raises ``RuntimeError`` if AgentMail is not configured.
    """
    global _client
    if _client is not None:
        return _client

    if not settings.agentmail_api_key:
        raise RuntimeError(
            "AgentMail API key not configured. "
            "Set LUCY_AGENTMAIL_API_KEY or add to keys.json."
        )

    _client = LucyEmailClient(
        api_key=settings.agentmail_api_key,
        domain=settings.agentmail_domain,
    )
    logger.info(
        "agentmail_client_initialized",
        domain=settings.agentmail_domain,
    )
    return _client
