"""Bolt authorize callback for multi-tenant token resolution.

Slack Bolt calls this function for every incoming event to resolve
the correct bot token, bot_id, and bot_user_id for the workspace.
"""

from __future__ import annotations

import structlog
from slack_bolt.authorization import AuthorizeResult

from lucy.core.token_store import get_bot_token

logger = structlog.get_logger()


async def multi_tenant_authorize(
    *,
    enterprise_id: str | None = None,
    team_id: str | None = None,
    **_kwargs: object,
) -> AuthorizeResult:
    """Resolve bot token for the requesting workspace.

    Bolt calls this on every request with `enterprise_id` and `team_id`.
    We look up the encrypted token from the DB (with cache), decrypt it,
    and return an AuthorizeResult.
    """
    effective_team = team_id or ""
    if not effective_team:
        logger.error("authorize_missing_team_id", enterprise_id=enterprise_id)
        raise ValueError("team_id is required for authorization")

    try:
        bot_token = await get_bot_token(effective_team)
    except LookupError:
        logger.error("authorize_no_token", team_id=effective_team)
        raise

    return AuthorizeResult(
        enterprise_id=enterprise_id or "",
        team_id=effective_team,
        bot_token=bot_token,
        bot_id="",
        bot_user_id="",
    )
