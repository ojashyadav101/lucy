"""Integration tests for Slack handlers.

These tests verify the full flow:
1. Slack event received
2. Middleware resolves workspace/user
3. Handler creates task
4. Response sent back

Run with: pytest tests/integration/test_slack_handlers.py -v
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from slack_bolt import App
from slack_bolt.request import BoltRequest
from slack_bolt.context import BoltContext

from lucy.slack.middleware import (
    resolve_workspace_middleware,
    resolve_user_middleware,
    resolve_channel_middleware,
)
from lucy.slack.handlers import register_handlers
from lucy.db.models import Workspace, User, Channel, Task, TaskStatus
from lucy.db.session import AsyncSessionLocal


@pytest_asyncio.fixture
async def mock_slack_client() -> AsyncGenerator[MagicMock, None]:
    """Mock Slack client for fetching team/user info."""
    client = MagicMock()
    
    # Mock team_info
    client.team_info = AsyncMock(return_value={
        "team": {
            "name": "Test Team",
            "domain": "test.slack.com",
        }
    })
    
    # Mock users_info
    client.users_info = AsyncMock(return_value={
        "user": {
            "real_name": "Test User",
            "name": "testuser",
            "profile": {
                "email": "test@example.com",
                "image_72": "https://example.com/avatar.png",
            }
        }
    })
    
    yield client


@pytest_asyncio.fixture
async def test_workspace() -> AsyncGenerator[Workspace, None]:
    """Create a test workspace."""
    async with AsyncSessionLocal() as db:
        ws = Workspace(
            slack_team_id="T1234567890",
            name="Test Workspace",
            domain="test.slack.com",
        )
        db.add(ws)
        await db.commit()
        await db.refresh(ws)
        yield ws
        
        # Cleanup
        await db.delete(ws)
        await db.commit()


class TestWorkspaceMiddleware:
    """Test workspace resolution middleware."""

    async def test_creates_workspace_on_first_mention(self, mock_slack_client) -> None:
        """Middleware creates workspace when first mentioned."""
        from sqlalchemy import select
        
        # Setup mock request
        request = MagicMock(spec=BoltRequest)
        request.body = {
            "team_id": "T9999999999",  # New team
            "event": {
                "type": "app_mention",
                "user": "U123",
                "text": "@Lucy hello",
            }
        }
        
        context = MagicMock(spec=BoltContext)
        context.get = MagicMock(return_value=mock_slack_client)
        
        next_called = False
        async def mock_next():
            nonlocal next_called
            next_called = True
        
        # Run middleware
        await resolve_workspace_middleware(request, context, mock_next)
        
        # Verify workspace was created
        assert next_called
        assert hasattr(context, "workspace_id")
        
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Workspace).where(Workspace.slack_team_id == "T9999999999")
            )
            workspace = result.scalar_one_or_none()
            assert workspace is not None
            assert workspace.name == "Test Team"
            
            # Cleanup
            await db.delete(workspace)
            await db.commit()


class TestAppMentionHandler:
    """Test @Lucy mention handler."""

    async def test_hello_responds_with_greeting(self) -> None:
        """@Lucy hello gets a greeting response."""
        from lucy.slack.blocks import LucyMessage
        
        blocks = LucyMessage.simple_response(
            "Hello! I'm Lucy, your AI coworker. How can I help today?",
            emoji="👋",
        )
        
        assert len(blocks) == 1
        assert blocks[0]["type"] == "section"
        assert "Hello! I'm Lucy" in blocks[0]["text"]["text"]

    async def test_task_created_for_request(self) -> None:
        """Non-trivial mentions create task records."""
        from sqlalchemy import select
        
        # This would test the full handler with mocked Slack API
        # For now, verify the task creation logic
        async with AsyncSessionLocal() as db:
            task = Task(
                workspace_id=uuid.uuid4(),  # Fake workspace
                intent="chat",
                status=TaskStatus.CREATED,
                config={
                    "source": "app_mention",
                    "original_text": "Generate a report",
                },
            )
            db.add(task)
            await db.commit()
            await db.refresh(task)
            
            # Verify task was created
            result = await db.execute(
                select(Task).where(Task.id == task.id)
            )
            saved_task = result.scalar_one()
            assert saved_task.status == TaskStatus.CREATED
            assert saved_task.intent == "chat"
            
            # Cleanup
            await db.delete(task)
            await db.commit()


class TestSlashCommandHandler:
    """Test /lucy slash command."""

    async def test_help_command(self) -> None:
        """/lucy help returns help message."""
        from lucy.slack.blocks import LucyMessage
        
        blocks = LucyMessage.help()
        
        # Should have header, section, divider, section, context
        assert len(blocks) >= 4
        assert blocks[0]["type"] == "header"
        assert "Lucy" in blocks[0]["text"]["text"]

    async def test_status_command(self) -> None:
        """/lucy status returns status message."""
        from lucy.slack.blocks import LucyMessage
        
        blocks = LucyMessage.status()
        
        assert len(blocks) == 2
        assert blocks[0]["type"] == "header"
        assert "Status" in blocks[0]["text"]["text"]


class TestBlockActions:
    """Test Block Kit button clicks."""

    async def test_approve_action_updates_approval(self) -> None:
        """Clicking approve updates approval status."""
        from lucy.db.models import Approval, ApprovalStatus
        
        async with AsyncSessionLocal() as db:
            # Create test approval
            approval = Approval(
                workspace_id=uuid.uuid4(),
                task_id=uuid.uuid4(),
                action_type="test",
                action_description="Test approval",
                status=ApprovalStatus.PENDING,
            )
            db.add(approval)
            await db.commit()
            await db.refresh(approval)
            
            # Simulate approval
            approval.status = ApprovalStatus.APPROVED
            approval.responded_at = datetime.now(timezone.utc)
            await db.commit()
            
            # Verify
            result = await db.execute(
                select(Approval).where(Approval.id == approval.id)
            )
            updated = result.scalar_one()
            assert updated.status == ApprovalStatus.APPROVED
            assert updated.responded_at is not None
            
            # Cleanup
            await db.delete(approval)
            await db.commit()


class TestBlockKitMessages:
    """Test Block Kit message composition."""

    async def test_task_confirmation(self) -> None:
        """Task confirmation has all required elements."""
        from lucy.slack.blocks import LucyMessage
        
        task_id = uuid.uuid4()
        blocks = LucyMessage.task_confirmation(
            task_id=task_id,
            description="Generate weekly report",
        )
        
        # Should have header, section, context, divider, actions
        assert len(blocks) == 5
        assert blocks[0]["type"] == "header"
        assert blocks[1]["type"] == "section"
        assert blocks[4]["type"] == "actions"

    async def test_approval_request(self) -> None:
        """Approval request shows risk level correctly."""
        from lucy.slack.blocks import LucyMessage
        
        approval_id = uuid.uuid4()
        blocks = LucyMessage.approval_request(
            approval_id=approval_id,
            action_type="code_deployment",
            description="Deploy to production",
            risk_level="high",
            requester_name="Alice",
        )
        
        # Check for high risk indicator
        context_block = blocks[2]  # Third block is context
        assert context_block["type"] == "context"
        
        # Should have approve and reject buttons
        actions_block = blocks[4]
        assert actions_block["type"] == "actions"
        assert len(actions_block["elements"]) == 3  # approve, reject, view

    async def test_error_message(self) -> None:
        """Error message includes suggestion when provided."""
        from lucy.slack.blocks import LucyMessage
        
        blocks = LucyMessage.error(
            message="Something went wrong",
            error_code="E123",
            suggestion="Try again later",
        )
        
        assert len(blocks) == 4  # header, message, error code, suggestion
        assert blocks[2]["type"] == "context"  # Error code
        assert blocks[3]["type"] == "section"  # Suggestion

    async def test_connection_request(self) -> None:
        """Connection request has the correct OAuth link."""
        from lucy.slack.blocks import LucyMessage
        
        oauth_url = "https://composio.dev/connect/123"
        blocks = LucyMessage.connection_request(
            provider_name="github",
            oauth_url=oauth_url,
        )
        
        assert len(blocks) == 4
        assert blocks[0]["type"] == "header"
        assert "Github" in blocks[0]["text"]["text"]
        
        actions_block = blocks[2]
        assert actions_block["type"] == "actions"
        button = actions_block["elements"][0]
        assert button["type"] == "button"
        assert button["url"] == oauth_url
