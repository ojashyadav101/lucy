"""Slack Bolt middleware for Lucy.

Resolves workspace_id and user_id from Slack events and attaches to context.
Creates workspaces/users on first encounter (lazy onboarding).
"""

from __future__ import annotations

from typing import Callable, Any
from uuid import UUID

from slack_bolt.request.async_request import AsyncBoltRequest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from lucy.db.models import Workspace, User, Channel
from lucy.db.session import AsyncSessionLocal
import structlog

logger = structlog.get_logger()


async def resolve_workspace_middleware(
    request: AsyncBoltRequest, context: Any, next: Callable[[], Any]
) -> None:
    """Resolve workspace_id from Slack team_id.
    
    Creates workspace on first encounter (lazy onboarding).
    Attaches workspace_id to context.
    """
    team_id = request.body.get("team_id") if request.body else None
    
    if not team_id:
        await next()
        return
    
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Workspace).where(
                Workspace.slack_team_id == team_id,
                Workspace.deleted_at.is_(None),
            )
        )
        workspace = result.scalar_one_or_none()
        
        if workspace is None:
            team_name = f"Team {team_id}"
            domain = None
            try:
                client = context.get("client")
                if client:
                    team_info = await client.team_info()
                    team_name = team_info.get("team", {}).get("name", team_name)
                    domain = team_info.get("team", {}).get("domain")
            except Exception:
                pass
            
            try:
                workspace = Workspace(
                    slack_team_id=team_id,
                    name=team_name,
                    domain=domain,
                )
                db.add(workspace)
                await db.commit()
                await db.refresh(workspace)
                logger.info("workspace_created", team_id=team_id, name=team_name)
            except IntegrityError:
                await db.rollback()
                # Race condition: another request created it, fetch it
                result2 = await db.execute(
                    select(Workspace).where(Workspace.slack_team_id == team_id)
                )
                workspace = result2.scalar_one()
        
        # Use dict-style context to avoid the setter restriction
        context["workspace_id"] = workspace.id
    
    await next()


async def resolve_user_middleware(
    request: AsyncBoltRequest, context: Any, next: Callable[[], Any]
) -> None:
    """Resolve user_id from Slack user_id.
    
    Creates user on first encounter. Attaches user_id to context.
    """
    slack_user_id = None
    
    if request.body:
        slack_user_id = (
            request.body.get("event", {}).get("user")
            or request.body.get("user_id")
            or request.body.get("user", {}).get("id")
        )
    
    if not slack_user_id or slack_user_id.startswith("B"):
        await next()
        return
    
    workspace_id: UUID | None = context.get("workspace_id")
    if not workspace_id:
        await next()
        return
    
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(User).where(
                User.workspace_id == workspace_id,
                User.slack_user_id == slack_user_id,
                User.deleted_at.is_(None),
            )
        )
        user = result.scalar_one_or_none()
        
        if user is None:
            display_name = f"User {slack_user_id}"
            email = None
            avatar_url = None
            try:
                client = context.get("client")
                if client:
                    user_info = await client.users_info(user=slack_user_id)
                    profile = user_info.get("user", {})
                    display_name = (
                        profile.get("real_name")
                        or profile.get("name")
                        or display_name
                    )
                    email = profile.get("profile", {}).get("email")
                    avatar_url = profile.get("profile", {}).get("image_72")
            except Exception:
                pass
            
            try:
                user = User(
                    workspace_id=workspace_id,
                    slack_user_id=slack_user_id,
                    display_name=display_name,
                    email=email,
                    avatar_url=avatar_url,
                )
                db.add(user)
                await db.commit()
                await db.refresh(user)
                logger.info("user_created", slack_user_id=slack_user_id, name=display_name)
            except IntegrityError:
                await db.rollback()
                result2 = await db.execute(
                    select(User).where(
                        User.workspace_id == workspace_id,
                        User.slack_user_id == slack_user_id,
                    )
                )
                user = result2.scalar_one()
        
        # Update last_seen_at without crashing if it errors
        try:
            user.touch()
            await db.commit()
        except Exception:
            await db.rollback()
        
        context["user_id"] = user.id
        context["slack_user_id"] = slack_user_id  # Store Slack user ID for thread tracking
    
    await next()


async def resolve_channel_middleware(
    request: AsyncBoltRequest, context: Any, next: Callable[[], Any]
) -> None:
    """Resolve channel from Slack channel_id.
    
    Creates channel record on first encounter. Attaches channel_id to context.
    """
    slack_channel_id = None
    
    if request.body:
        slack_channel_id = (
            request.body.get("event", {}).get("channel")
            or request.body.get("channel_id")
            or request.body.get("channel", {}).get("id")
        )
    
    if not slack_channel_id:
        await next()
        return
    
    workspace_id: UUID | None = context.get("workspace_id")
    if not workspace_id:
        await next()
        return
    
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Channel).where(
                Channel.workspace_id == workspace_id,
                Channel.slack_channel_id == slack_channel_id,
                Channel.deleted_at.is_(None),
            )
        )
        channel = result.scalar_one_or_none()
        
        if channel is None:
            try:
                channel = Channel(
                    workspace_id=workspace_id,
                    slack_channel_id=slack_channel_id,
                    name=f"Channel {slack_channel_id}",
                    channel_type="unknown",
                    memory_scope_key=f"ch:{slack_channel_id}",
                )
                db.add(channel)
                await db.commit()
                await db.refresh(channel)
            except IntegrityError:
                await db.rollback()
                result2 = await db.execute(
                    select(Channel).where(
                        Channel.workspace_id == workspace_id,
                        Channel.slack_channel_id == slack_channel_id,
                    )
                )
                channel = result2.scalar_one()
        
        context["channel_id"] = channel.id
    
    await next()
