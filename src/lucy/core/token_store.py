"""Per-workspace Slack bot token store with in-memory TTL cache.

Provides `get_slack_client(workspace_id)` to resolve the correct bot
token for any workspace, falling back to the global static token when
no workspace-specific token exists (single-tenant backwards compat).
"""

from __future__ import annotations

import asyncio
import time
import uuid

import structlog
from slack_sdk.web.async_client import AsyncWebClient
from sqlalchemy import select

from lucy.config import settings
from lucy.core.crypto import decrypt
from lucy.db.models import Workspace
from lucy.db.session import db_session

logger = structlog.get_logger()

_TOKEN_TTL = 300  # 5 min
_cache: dict[str, tuple[str, float]] = {}  # team_id -> (token, expires_at)
_client_cache: dict[str, tuple[AsyncWebClient, float]] = {}
_lock = asyncio.Lock()


async def get_bot_token(team_id: str) -> str:
    """Resolve the bot token for a Slack team, with caching."""
    now = time.monotonic()

    cached = _cache.get(team_id)
    if cached and cached[1] > now:
        return cached[0]

    async with _lock:
        cached = _cache.get(team_id)
        if cached and cached[1] > now:
            return cached[0]

        token = await _fetch_token_from_db(team_id)
        if token:
            _cache[team_id] = (token, now + _TOKEN_TTL)
            return token

    if settings.slack_bot_token:
        return settings.slack_bot_token

    raise LookupError(f"No bot token found for team_id={team_id}")


async def get_slack_client(team_id: str) -> AsyncWebClient:
    """Get an AsyncWebClient for the given workspace, cached."""
    now = time.monotonic()

    cached = _client_cache.get(team_id)
    if cached and cached[1] > now:
        return cached[0]

    token = await get_bot_token(team_id)
    client = AsyncWebClient(token=token)
    _client_cache[team_id] = (client, now + _TOKEN_TTL)
    return client


async def _fetch_token_from_db(team_id: str) -> str | None:
    """Load and decrypt bot token from the database."""
    try:
        async with db_session() as session:
            result = await session.execute(
                select(Workspace.slack_bot_token_encrypted)
                .where(
                    Workspace.slack_team_id == team_id,
                    Workspace.is_active.is_(True),
                )
            )
            encrypted = result.scalar_one_or_none()
            if encrypted:
                return decrypt(encrypted)
    except Exception:
        logger.exception("token_fetch_failed", team_id=team_id)
    return None


async def store_bot_token(
    team_id: str,
    bot_token: str,
    *,
    team_name: str = "",
    team_domain: str = "",
    bot_user_id: str = "",
) -> uuid.UUID:
    """Encrypt and store (or update) a workspace bot token.

    Returns the workspace UUID.
    """
    from lucy.core.crypto import encrypt

    encrypted = encrypt(bot_token)

    async with db_session() as session:
        result = await session.execute(
            select(Workspace).where(Workspace.slack_team_id == team_id)
        )
        ws = result.scalar_one_or_none()

        if ws:
            ws.slack_bot_token_encrypted = encrypted
            ws.slack_bot_user_id = bot_user_id or ws.slack_bot_user_id
            ws.slack_team_name = team_name or ws.slack_team_name
            ws.slack_team_domain = team_domain or ws.slack_team_domain
            ws.is_active = True
        else:
            ws = Workspace(
                slack_team_id=team_id,
                name=team_name or team_id,
                domain=team_domain,
                slack_bot_token_encrypted=encrypted,
                slack_bot_user_id=bot_user_id,
                slack_team_name=team_name,
                slack_team_domain=team_domain,
                is_active=True,
            )
            session.add(ws)

        await session.flush()
        ws_id = ws.id

    _cache.pop(team_id, None)
    _client_cache.pop(team_id, None)
    logger.info("bot_token_stored", team_id=team_id, workspace_id=str(ws_id))
    return ws_id


def invalidate_cache(team_id: str) -> None:
    """Remove cached token/client for a workspace (e.g. on uninstall)."""
    _cache.pop(team_id, None)
    _client_cache.pop(team_id, None)
