"""Slack OAuth v2 flow — Add to Slack + callback.

Endpoints:
  GET  /slack/oauth/start    → redirect to Slack's authorize URL
  GET  /slack/oauth/callback → exchange code for bot token, provision workspace
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

import httpx
import structlog
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from lucy.config import settings
from lucy.core.token_store import store_bot_token
from lucy.db.models import OAuthState
from lucy.db.session import db_session

logger = structlog.get_logger()

router = APIRouter(tags=["oauth"])

_SLACK_AUTHORIZE_URL = "https://slack.com/oauth/v2/authorize"
_SLACK_TOKEN_URL = "https://slack.com/api/oauth.v2.access"

BOT_SCOPES = ",".join([
    "app_mentions:read",
    "channels:history",
    "channels:join",
    "channels:read",
    "chat:write",
    "commands",
    "files:read",
    "groups:history",
    "groups:read",
    "im:history",
    "im:read",
    "im:write",
    "mpim:history",
    "mpim:read",
    "reactions:read",
    "reactions:write",
    "team:read",
    "users:read",
    "users:read.email",
])


@router.get("/slack/oauth/start")
async def oauth_start() -> RedirectResponse:
    """Generate a CSRF state, persist it, and redirect to Slack."""
    if not settings.slack_client_id:
        raise HTTPException(500, "LUCY_SLACK_CLIENT_ID not configured")

    state = secrets.token_urlsafe(48)
    expires = datetime.now(UTC) + timedelta(minutes=10)

    async with db_session() as session:
        session.add(OAuthState(state=state, expires_at=expires))

    redirect_uri = f"https://app.zeeya.ai/slack/oauth/callback"
    url = (
        f"{_SLACK_AUTHORIZE_URL}"
        f"?client_id={settings.slack_client_id}"
        f"&scope={BOT_SCOPES}"
        f"&redirect_uri={redirect_uri}"
        f"&state={state}"
    )
    return RedirectResponse(url, status_code=302)


@router.get("/slack/oauth/callback")
async def oauth_callback(
    code: str = Query(...),
    state: str = Query(...),
    error: str | None = Query(None),
) -> dict:
    """Exchange the Slack auth code for a bot token and provision workspace."""
    if error:
        logger.warning("oauth_callback_error", error=error)
        raise HTTPException(400, f"Slack OAuth error: {error}")

    if not await _verify_state(state):
        raise HTTPException(400, "Invalid or expired OAuth state")

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            _SLACK_TOKEN_URL,
            data={
                "client_id": settings.slack_client_id,
                "client_secret": settings.slack_client_secret,
                "code": code,
                "redirect_uri": "https://app.zeeya.ai/slack/oauth/callback",
            },
        )
        data = resp.json()

    if not data.get("ok"):
        logger.error("oauth_token_exchange_failed", error=data.get("error"))
        raise HTTPException(400, f"Token exchange failed: {data.get('error')}")

    team = data.get("team", {})
    team_id = team.get("id", "")
    team_name = team.get("name", "")
    bot_token = data.get("access_token", "")
    bot_user_id = data.get("bot_user_id", "")

    if not bot_token or not team_id:
        raise HTTPException(400, "Missing bot token or team ID in response")

    authed_user = data.get("authed_user", {})
    installing_user_id = authed_user.get("id", "")

    team_domain = ""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            info_resp = await client.post(
                "https://slack.com/api/team.info",
                headers={"Authorization": f"Bearer {bot_token}"},
            )
            info_data = info_resp.json()
            if info_data.get("ok"):
                team_domain = info_data.get("team", {}).get("domain", "")
    except Exception:
        logger.warning("team_info_fetch_failed", team_id=team_id)

    workspace_id = await store_bot_token(
        team_id=team_id,
        bot_token=bot_token,
        team_name=team_name,
        team_domain=team_domain,
        bot_user_id=bot_user_id,
    )

    await _provision_workspace(
        workspace_id=workspace_id,
        team_id=team_id,
        team_name=team_name,
        team_domain=team_domain,
        installing_user_id=installing_user_id,
        bot_token=bot_token,
    )

    logger.info(
        "oauth_install_complete",
        team_id=team_id,
        team_name=team_name,
        workspace_id=str(workspace_id),
    )

    return {
        "ok": True,
        "message": f"Lucy installed in {team_name}!",
        "workspace_id": str(workspace_id),
    }


async def _verify_state(state: str) -> bool:
    """Verify CSRF state exists and hasn't expired, then delete it."""
    async with db_session() as session:
        result = await session.execute(
            select(OAuthState).where(
                OAuthState.state == state,
                OAuthState.expires_at > datetime.now(UTC),
            )
        )
        row = result.scalar_one_or_none()
        if not row:
            return False
        await session.delete(row)
    return True


async def _provision_workspace(
    *,
    workspace_id: object,
    team_id: str,
    team_name: str,
    team_domain: str,
    installing_user_id: str,
    bot_token: str,
) -> None:
    """Post-install workspace provisioning.

    Creates filesystem workspace dir, seeds default skills,
    provisions AgentMail inbox. Runs in-band for now (fast);
    can be moved to a background task if it grows.
    """
    from lucy.workspace.filesystem import WorkspaceFS

    ws_fs = WorkspaceFS(str(workspace_id), settings.workspace_root)
    await ws_fs.ensure_structure()

    slug = _sanitize_email_slug(team_domain or team_name or team_id)
    agent_email = f"{slug}@{settings.agentmail_domain}"

    if settings.agentmail_enabled and settings.agentmail_api_key:
        try:
            await _create_agentmail_inbox(agent_email)
        except Exception:
            logger.warning(
                "agentmail_inbox_creation_failed",
                agent_email=agent_email,
                workspace_id=str(workspace_id),
            )

    async with db_session() as session:
        from lucy.db.models import Workspace
        result = await session.execute(
            select(Workspace).where(Workspace.id == workspace_id)
        )
        ws = result.scalar_one_or_none()
        if ws:
            ws.agent_email = agent_email
            from datetime import UTC, datetime
            ws.installed_at = datetime.now(UTC)

    try:
        from slack_sdk.web.async_client import AsyncWebClient
        client = AsyncWebClient(token=bot_token)
        channels = await client.conversations_list(types="public_channel", limit=200)
        for ch in channels.get("channels", []):
            name = ch.get("name", "")
            if name in ("general", "talk-to-lucy", "lucy", "lucy-my-ai"):
                try:
                    await client.conversations_join(channel=ch["id"])
                except Exception:
                    pass
    except Exception:
        logger.warning("auto_join_channels_failed", workspace_id=str(workspace_id))


def _sanitize_email_slug(raw: str) -> str:
    """Turn a team domain/name into a safe email local part."""
    import re
    slug = raw.lower().strip()
    slug = re.sub(r"[^a-z0-9._-]", "", slug)
    slug = slug.strip(".-_") or "workspace"
    return slug[:64]


async def _create_agentmail_inbox(address: str) -> None:
    """Create an AgentMail inbox for the workspace."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            "https://api.agentmail.to/v0/inboxes",
            headers={"Authorization": f"Bearer {settings.agentmail_api_key}"},
            json={"address": address},
        )
        if resp.status_code == 409:
            logger.info("agentmail_inbox_exists", address=address)
            return
        resp.raise_for_status()
        logger.info("agentmail_inbox_created", address=address)
