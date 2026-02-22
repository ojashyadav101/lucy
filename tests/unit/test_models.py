"""Unit tests for database models.

Run with: pytest tests/unit/test_models.py -v
"""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from lucy.db.models import (
    Workspace,
    User,
    Channel,
    Agent,
    Task,
    TaskStatus,
    TaskPriority,
    Approval,
    ApprovalStatus,
)
from lucy.db.session import db_session


@pytest.fixture
async def workspace() -> Workspace:
    """Create a test workspace."""
    async with db_session() as db:
        ws = Workspace(
            slack_team_id="T1234567890",
            name="Test Workspace",
            domain="test.slack.com",
        )
        db.add(ws)
        await db.flush()
        await db.refresh(ws)
        return ws


@pytest.fixture
async def user(workspace: Workspace) -> User:
    """Create a test user."""
    async with db_session() as db:
        u = User(
            workspace_id=workspace.id,
            slack_user_id="U1234567890",
            display_name="Test User",
            email="test@example.com",
        )
        db.add(u)
        await db.flush()
        await db.refresh(u)
        return u


class TestWorkspace:
    """Test Workspace model."""

    async def test_create_workspace(self) -> None:
        """Workspace can be created with default values."""
        async with db_session() as db:
            ws = Workspace(
                slack_team_id="T9999999999",
                name="Another Test",
            )
            db.add(ws)
            await db.flush()

            assert ws.id is not None
            assert isinstance(ws.id, uuid.UUID)
            assert ws.plan == "starter"
            assert ws.status == "active"
            assert ws.max_users == 5
            assert ws.settings == {}

    async def test_workspace_unique_slack_team(self, workspace: Workspace) -> None:
        """Cannot create duplicate slack_team_id."""
        async with db_session() as db:
            duplicate = Workspace(
                slack_team_id="T1234567890",  # Same as fixture
                name="Duplicate",
            )
            db.add(duplicate)
            with pytest.raises(Exception):  # IntegrityError
                await db.flush()


class TestUser:
    """Test User model."""

    async def test_user_workspace_relationship(self, workspace: Workspace) -> None:
        """User belongs to workspace."""
        async with db_session() as db:
            u = User(
                workspace_id=workspace.id,
                slack_user_id="U9999999999",
                display_name="Another User",
            )
            db.add(u)
            await db.flush()

            assert u.workspace_id == workspace.id
            assert u.role == "member"
            assert u.is_active is True

    async def test_user_preferences_jsonb(self, workspace: Workspace) -> None:
        """JSONB preferences work correctly."""
        async with db_session() as db:
            u = User(
                workspace_id=workspace.id,
                slack_user_id="U8888888888",
                display_name="Prefs User",
                preferences={
                    "timezone": "America/New_York",
                    "notifications": {"email": True, "slack": False},
                    "model_tier": "fast",
                },
            )
            db.add(u)
            await db.flush()

            assert u.preferences["timezone"] == "America/New_York"
            assert u.preferences["notifications"]["email"] is True


class TestTask:
    """Test Task model."""

    async def test_task_lifecycle(self, workspace: Workspace, user: User) -> None:
        """Task lifecycle states work correctly."""
        async with db_session() as db:
            task = Task(
                workspace_id=workspace.id,
                requester_id=user.id,
                intent="lookup",
                priority=TaskPriority.HIGH,
                status=TaskStatus.CREATED,
                config={"tools": ["slack", "notion"]},
            )
            db.add(task)
            await db.flush()

            assert task.status == TaskStatus.CREATED
            assert task.priority == TaskPriority.HIGH
            assert task.config["tools"] == ["slack", "notion"]

            # Transition to running
            task.status = TaskStatus.RUNNING
            task.started_at = datetime.now(timezone.utc)
            await db.flush()

            assert task.status == TaskStatus.RUNNING
            assert task.started_at is not None

    async def test_task_result_data(self, workspace: Workspace) -> None:
        """Task can store structured results."""
        async with db_session() as db:
            task = Task(
                workspace_id=workspace.id,
                intent="report",
                status=TaskStatus.COMPLETED,
                result_summary="Weekly sales report generated",
                result_data={
                    "total_revenue": 50000.00,
                    "orders": 150,
                    "top_product": "Widget Pro",
                },
            )
            db.add(task)
            await db.flush()

            assert task.result_data["total_revenue"] == 50000.00


class TestApproval:
    """Test Approval model."""

    async def test_approval_workflow(
        self, workspace: Workspace, user: User
    ) -> None:
        """Approval request lifecycle."""
        async with db_session() as db:
            # Create a task that needs approval
            task = Task(
                workspace_id=workspace.id,
                requester_id=user.id,
                intent="code_deployment",
                status=TaskStatus.PENDING_APPROVAL,
            )
            db.add(task)
            await db.flush()

            # Create approval
            approval = Approval(
                workspace_id=workspace.id,
                task_id=task.id,
                approver_id=user.id,
                action_type="code_deployment",
                action_description="Deploy new feature to production",
                risk_level="high",
                status=ApprovalStatus.PENDING,
            )
            db.add(approval)
            await db.flush()

            assert approval.status == ApprovalStatus.PENDING
            assert approval.risk_level == "high"

            # Approve
            approval.status = ApprovalStatus.APPROVED
            approval.responded_at = datetime.now(timezone.utc)
            approval.response = "LGTM, approved for deploy"
            await db.flush()

            assert approval.status == ApprovalStatus.APPROVED


class TestQueryPatterns:
    """Test database query patterns (simulating production usage)."""

    async def test_active_tasks_query(self, workspace: Workspace) -> None:
        """Query pattern: Find all active tasks for a workspace."""
        async with db_session() as db:
            # Create tasks in various states
            for i in range(5):
                task = Task(
                    workspace_id=workspace.id,
                    status=TaskStatus.RUNNING if i < 2 else TaskStatus.COMPLETED,
                )
                db.add(task)
            await db.flush()

            # Query active tasks (simulating partial index usage)
            result = await db.execute(
                select(Task).where(
                    Task.workspace_id == workspace.id,
                    Task.status.in_([
                        TaskStatus.CREATED,
                        TaskStatus.PENDING_APPROVAL,
                        TaskStatus.RUNNING,
                    ]),
                )
            )
            active_tasks = result.scalars().all()

            assert len(active_tasks) == 2

    async def test_pending_approvals_query(self, user: User) -> None:
        """Query pattern: Find pending approvals for a user."""
        async with db_session() as db:
            # Create approvals
            for i in range(3):
                task = Task(
                    workspace_id=user.workspace_id,
                    status=TaskStatus.PENDING_APPROVAL,
                )
                db.add(task)
                await db.flush()

                approval = Approval(
                    workspace_id=user.workspace_id,
                    task_id=task.id,
                    approver_id=user.id,
                    action_type="tool_execution",
                    action_description=f"Action {i}",
                    status=ApprovalStatus.PENDING if i < 2 else ApprovalStatus.APPROVED,
                )
                db.add(approval)
            await db.flush()

            # Query pending approvals for user
            result = await db.execute(
                select(Approval).where(
                    Approval.approver_id == user.id,
                    Approval.status == ApprovalStatus.PENDING,
                )
            )
            pending = result.scalars().all()

            assert len(pending) == 2
