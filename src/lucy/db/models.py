"""Lucy database models — production-grade multi-tenant schema.

Design principles:
- All tables partitioned by workspace_id for tenant isolation
- JSONB columns for flexible, evolving schemas without migrations
- Time-series tables partitioned by time (cost_log, audit_log)
- Soft deletes everywhere (deleted_at) for audit trail
- Encrypted columns for credentials (pgsodium)
- Partial indexes for hot paths (active tasks, pending approvals)
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB as _JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


class JSONB(TypeDecorator):
    """Custom JSONB type that handles UUID, datetime, and Enum serialization."""
    
    impl = _JSONB
    cache_ok = True
    
    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        """Convert Python objects to JSON-serializable format before storing."""
        if value is None:
            return None
        return json.loads(json.dumps(value, default=self._json_default))
    
    def process_result_value(self, value: Any, dialect: Any) -> Any:
        """Return value as-is (already deserialized by asyncpg)."""
        return value
    
    @staticmethod
    def _json_default(obj: Any) -> Any:
        """JSON serializer for objects not serializable by default."""
        if isinstance(obj, uuid.UUID):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, Enum):
            return obj.value
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


class Base(DeclarativeBase):
    """Base class with common utilities."""

    type_annotation_map = {
        dict[str, Any]: JSONB,
        list[str]: JSONB,
        datetime: DateTime(timezone=True),
    }

    def to_dict(self) -> dict[str, Any]:
        """Convert model to dict for serialization."""
        return {
            c.name: getattr(self, c.name)
            for c in self.__table__.columns
        }


# ═══════════════════════════════════════════════════════════════════════════════
# ENUMS
# ═══════════════════════════════════════════════════════════════════════════════

class TaskStatus(str, Enum):
    """Task lifecycle states."""
    CREATED = "created"              # Initial state
    PENDING_APPROVAL = "pending_approval"  # Waiting for human
    RUNNING = "running"              # Actively executing
    COMPLETED = "completed"          # Success
    FAILED = "failed"                # Error occurred
    CANCELLED = "cancelled"          # Explicitly cancelled
    TIMEOUT = "timeout"             # Exceeded time limit


class TaskPriority(str, Enum):
    """Task priority levels."""
    CRITICAL = "critical"   # Immediate execution
    HIGH = "high"       # Queue head
    NORMAL = "normal"     # Default
    LOW = "low"        # Queue tail
    BATCH = "batch"      # Background processing


class ApprovalStatus(str, Enum):
    """Approval request states."""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class IntegrationStatus(str, Enum):
    """External integration health."""
    PENDING = "pending"      # OAuth flow started
    ACTIVE = "active"          # Working
    REFRESHING = "refreshing"  # Token renewal in progress
    ERROR = "error"            # Credential or API issue
    REVOKED = "revoked"        # User disconnected


class HeartbeatStatus(str, Enum):
    """Monitor condition evaluation result."""
    HEALTHY = "healthy"      # Condition not triggered
    TRIGGERED = "triggered"  # Condition met, alerted
    SILENCED = "silenced"    # In quiet period
    DISABLED = "disabled"    # Manually turned off


# ═══════════════════════════════════════════════════════════════════════════════
# CORE TENANT TABLES
# ═══════════════════════════════════════════════════════════════════════════════

class Workspace(Base):
    """Multi-tenant workspace — the top-level isolation boundary."""
    
    __tablename__ = "workspaces"
    __table_args__ = (
        Index("ix_workspaces_slack_team_id", "slack_team_id", unique=True),
        Index("ix_workspaces_status", "status", "created_at"),
        {"comment": "Tenant isolation boundary"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    slack_team_id: Mapped[str] = mapped_column(
        String(32), unique=True, nullable=False,
        comment="Slack workspace/team ID (T1234567890)"
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    
    # Plan & limits
    plan: Mapped[str] = mapped_column(
        String(20), default="starter", nullable=False,
        comment="starter|pro|enterprise"
    )
    status: Mapped[str] = mapped_column(
        String(20), default="active", nullable=False,
        comment="active|suspended|deleted"
    )
    
    # Feature flags (JSONB for flexibility)
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict,
        comment="Feature toggles, rate limits, custom config"
    )
    
    # Quotas (enforced in application layer)
    max_users: Mapped[int] = mapped_column(Integer, default=5)
    max_monthly_actions: Mapped[int] = mapped_column(Integer, default=500)
    max_integrations: Mapped[int] = mapped_column(Integer, default=3)
    
    # Usage tracking (updated by background job)
    current_month_actions: Mapped[int] = mapped_column(Integer, default=0)
    current_month_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), default=Decimal("0.0000")
    )
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        server_default=func.now(), 
        onupdate=func.now(),
        nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="Soft delete timestamp"
    )

    # Relationships
    users: Mapped[list["User"]] = relationship(back_populates="workspace")
    channels: Mapped[list["Channel"]] = relationship(back_populates="workspace")
    tasks: Mapped[list["Task"]] = relationship(back_populates="workspace")
    agents: Mapped[list["Agent"]] = relationship(back_populates="workspace")


class User(Base):
    """Workspace member (human or bot)."""
    
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("workspace_id", "slack_user_id", name="uix_user_workspace_slack"),
        Index("ix_users_workspace_role", "workspace_id", "role"),
        {"comment": "Workspace members"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    slack_user_id: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="Slack user ID (U1234567890)"
    )
    
    # Profile
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    
    # Role & permissions
    role: Mapped[str] = mapped_column(
        String(20), default="member", nullable=False,
        comment="owner|admin|member|guest"
    )
    
    # Preferences (JSONB for flexibility)
    preferences: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict,
        comment="User-specific settings: timezone, notification prefs, model tier preference"
    )
    
    # AI-specific (V2 personal agents)
    has_personal_agent: Mapped[bool] = mapped_column(Boolean, default=False)
    personal_agent_config: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True,
        comment="Personal agent settings (V2 feature)"
    )
    
    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Relationships
    workspace: Mapped[Workspace] = relationship(back_populates="users")
    tasks_requested: Mapped[list["Task"]] = relationship(
        foreign_keys="Task.requester_id", back_populates="requester"
    )

    def touch(self) -> None:
        """Update last_seen_at to now."""
        self.last_seen_at = datetime.now(timezone.utc)


class Channel(Base):
    """Slack channel with Lucy membership tracking."""
    
    __tablename__ = "channels"
    __table_args__ = (
        UniqueConstraint("workspace_id", "slack_channel_id", name="uix_channel_workspace_slack"),
        Index("ix_channels_workspace_type", "workspace_id", "channel_type"),
        {"comment": "Slack channels where Lucy operates"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    slack_channel_id: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="Slack channel ID (C1234567890)"
    )
    
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    channel_type: Mapped[str] = mapped_column(
        String(20), nullable=False,
        comment="public|private|im|mpim"
    )
    
    # Lucy membership
    lucy_joined_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_monitored: Mapped[bool] = mapped_column(
        Boolean, default=True,
        comment="Whether to read history for patterns"
    )
    
    # Channel-specific settings (override workspace defaults)
    settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict,
        comment="Channel-specific config: require_approval, model_tier"
    )
    
    # Memory scoping (used for Qdrant namespace)
    memory_scope_key: Mapped[str] = mapped_column(
        String(255), nullable=False,
        comment="Qdrant namespace: ch:{team_id}:{channel_id}"
    )
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    workspace: Mapped[Workspace] = relationship(back_populates="channels")


# ═══════════════════════════════════════════════════════════════════════════════
# AGENT TABLES (V2: Supports personal agents)
# ═══════════════════════════════════════════════════════════════════════════════

class Agent(Base):
    """Lucy or personal agent instance within a workspace."""
    
    __tablename__ = "agents"
    __table_args__ = (
        Index("ix_agents_workspace_type", "workspace_id", "agent_type"),
        {"comment": "Agent instances (Lucy main + future personal agents)"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    
    # Agent classification
    agent_type: Mapped[str] = mapped_column(
        String(20), nullable=False,
        comment="main|personal|custom"
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    
    # For personal agents (V2)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    
    # Configuration
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict,
        comment="Agent-specific settings: model preferences, tool allowlist, personality"
    )
    
    # State
    status: Mapped[str] = mapped_column(
        String(20), default="active", nullable=False,
        comment="active|paused|deleted"
    )
    
    # Resource tracking
    total_tasks_completed: Mapped[int] = mapped_column(Integer, default=0)
    total_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), default=Decimal("0.0000")
    )
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    workspace: Mapped[Workspace] = relationship(back_populates="agents")


# ═══════════════════════════════════════════════════════════════════════════════
# TASK ORCHESTRATION TABLES
# ═══════════════════════════════════════════════════════════════════════════════

class Task(Base):
    """Primary work unit — tracks everything Lucy does."""
    
    __tablename__ = "tasks"
    __table_args__ = (
        # Critical indexes for performance
        Index("ix_tasks_workspace_status", "workspace_id", "status"),
        Index("ix_tasks_workspace_created", "workspace_id", "created_at"),
        Index("ix_tasks_requester", "requester_id", "created_at"),
        Index("ix_tasks_agent_status", "agent_id", "status"),
        # Partial index for active tasks (hot path)
        Index(
            "ix_tasks_active",
            "workspace_id", "status",
            postgresql_where="status IN ('created', 'pending_approval', 'running')"
        ),
        {"comment": "Work units with full lifecycle tracking"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    requester_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
        comment="User who requested this task"
    )
    
    # Slack context
    channel_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("channels.id", ondelete="SET NULL"), nullable=True
    )
    slack_thread_ts: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
        comment="Slack thread timestamp for replies"
    )
    
    # Classification
    intent: Mapped[str | None] = mapped_column(
        String(100), nullable=True,
        comment="Classified intent: lookup|tool_use|reasoning|code|chat"
    )
    model_tier: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
        comment="Model tier used: TIER_1_FAST|TIER_2_STANDARD|TIER_3_FRONTIER"
    )
    priority: Mapped[TaskPriority] = mapped_column(
        SQLEnum(TaskPriority, values_callable=lambda x: [e.value for e in x]),
        default=TaskPriority.NORMAL, nullable=False
    )
    
    # Status tracking
    status: Mapped[TaskStatus] = mapped_column(
        SQLEnum(TaskStatus, values_callable=lambda x: [e.value for e in x]),
        default=TaskStatus.CREATED, nullable=False
    )
    status_reason: Mapped[str | None] = mapped_column(
        Text, nullable=True,
        comment="Why status changed (error message, approval denial reason)"
    )
    
    # Timing
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
        comment="When task should complete by"
    )
    
    # Configuration (flexible per task type)
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict,
        comment="Task-specific config: tools to use, approval requirements"
    )
    
    # Results
    result_summary: Mapped[str | None] = mapped_column(
        Text, nullable=True,
        comment="Human-readable result summary"
    )
    result_data: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True,
        comment="Structured result data"
    )
    
    # Error tracking
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    
    # Relationships
    workspace: Mapped[Workspace] = relationship(back_populates="tasks")
    requester: Mapped[User | None] = relationship(
        foreign_keys=[requester_id], back_populates="tasks_requested"
    )
    steps: Mapped[list["TaskStep"]] = relationship(back_populates="task")
    approval: Mapped["Approval"] = relationship(back_populates="task")


class TaskStep(Base):
    """Granular step tracking for complex multi-step tasks."""
    
    __tablename__ = "task_steps"
    __table_args__ = (
        Index("ix_task_steps_task_seq", "task_id", "sequence_number"),
        {"comment": "Individual steps within a task"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    step_type: Mapped[str] = mapped_column(
        String(50), nullable=False,
        comment="llm_call|tool_use|approval_wait|sub_agent|sleep"
    )
    
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    
    # Timing
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    
    # Configuration & result
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    
    # Error tracking
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    task: Mapped[Task] = relationship(back_populates="steps")


# ═══════════════════════════════════════════════════════════════════════════════
# APPROVAL WORKFLOW
# ═══════════════════════════════════════════════════════════════════════════════

class Approval(Base):
    """Human-in-the-loop approval request."""
    
    __tablename__ = "approvals"
    __table_args__ = (
        Index("ix_approvals_workspace_status", "workspace_id", "status"),
        Index("ix_approvals_approver", "approver_id", "status"),
        # Hot path: pending approvals for a user
        Index(
            "ix_approvals_pending",
            "approver_id",
            postgresql_where="status = 'pending'"
        ),
        {"comment": "Human-in-the-loop approvals"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    
    # Who must approve
    approver_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    
    # What needs approval
    action_type: Mapped[str] = mapped_column(
        String(50), nullable=False,
        comment="tool_execution|code_deployment|message_send|data_export"
    )
    action_description: Mapped[str] = mapped_column(Text, nullable=False)
    
    # Risk classification
    risk_level: Mapped[str] = mapped_column(
        String(20), default="medium", nullable=False,
        comment="low|medium|high|critical"
    )
    
    # Status
    status: Mapped[ApprovalStatus] = mapped_column(
        SQLEnum(ApprovalStatus, values_callable=lambda x: [e.value for e in x]),
        default=ApprovalStatus.PENDING, nullable=False
    )
    
    # Slack delivery tracking
    slack_message_ts: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
        comment="Timestamp of Block Kit message in Slack"
    )
    
    # Response
    response: Mapped[str | None] = mapped_column(
        Text, nullable=True,
        comment="User's response text (if any)"
    )
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    
    # Expiration
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    task: Mapped[Task] = relationship(back_populates="approval")


# ═══════════════════════════════════════════════════════════════════════════════
# SCHEDULING & MONITORING
# ═══════════════════════════════════════════════════════════════════════════════

class Schedule(Base):
    """Cron-like scheduled workflows."""
    
    __tablename__ = "schedules"
    __table_args__ = (
        Index("ix_schedules_workspace_active", "workspace_id", "is_active"),
        Index("ix_schedules_next_run", "next_run_at"),
        {"comment": "Recurring scheduled tasks"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    
    # Cron expression (e.g., "0 9 * * MON" for Monday 9am)
    cron_expression: Mapped[str] = mapped_column(String(100), nullable=False)
    timezone: Mapped[str] = mapped_column(String(50), default="UTC")
    
    # What to do
    intent_template: Mapped[str] = mapped_column(
        Text, nullable=False,
        comment="Natural language template for what to execute"
    )
    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict,
        comment="Schedule-specific config"
    )
    
    # Target (optional specific channel)
    target_channel_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("channels.id", ondelete="SET NULL"), nullable=True
    )
    
    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    
    # Execution tracking
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    
    run_count: Mapped[int] = mapped_column(Integer, default=0)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Heartbeat(Base):
    """Proactive monitoring condition."""
    
    __tablename__ = "heartbeats"
    __table_args__ = (
        Index("ix_heartbeats_workspace_active", "workspace_id", "is_active"),
        {"comment": "Condition-based monitoring"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    
    # Condition definition
    condition_type: Mapped[str] = mapped_column(
        String(50), nullable=False,
        comment="metric_threshold|api_health|schedule_miss|custom"
    )
    condition_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False,
        comment="{metric: 'error_rate', operator: '>', threshold: 2.0}"
    )
    
    # Check interval
    check_interval_seconds: Mapped[int] = mapped_column(
        Integer, default=300, nullable=False,
        comment="How often to evaluate (seconds)"
    )
    
    # Alerting
    alert_channel_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("channels.id", ondelete="SET NULL"), nullable=True
    )
    alert_template: Mapped[str] = mapped_column(
        Text, nullable=False,
        default="Condition triggered: {name}"
    )
    
    # Cooldown (prevent spam)
    alert_cooldown_seconds: Mapped[int] = mapped_column(Integer, default=3600)
    last_alert_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    
    # Status
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    current_status: Mapped[HeartbeatStatus] = mapped_column(
        SQLEnum(HeartbeatStatus, values_callable=lambda x: [e.value for e in x]),
        default=HeartbeatStatus.HEALTHY
    )
    
    # Statistics
    check_count: Mapped[int] = mapped_column(Integer, default=0)
    trigger_count: Mapped[int] = mapped_column(Integer, default=0)
    
    last_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_check_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ═══════════════════════════════════════════════════════════════════════════════
# INTEGRATIONS
# ═══════════════════════════════════════════════════════════════════════════════

class Integration(Base):
    """External tool connection (Linear, GitHub, etc.)."""
    
    __tablename__ = "integrations"
    __table_args__ = (
        Index("ix_integrations_workspace_provider", "workspace_id", "provider"),
        Index("ix_integrations_status", "workspace_id", "status"),
        {"comment": "External tool connections"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    
    provider: Mapped[str] = mapped_column(
        String(50), nullable=False,
        comment="linear|github|notion|stripe|hubspot|..."
    )
    
    # Connection metadata
    external_account_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True,
        comment="User/team ID in the external system"
    )
    external_account_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    
    # OAuth scopes granted
    scopes: Mapped[list[str]] = mapped_column(JSONB, default=list)
    
    # Status
    status: Mapped[IntegrationStatus] = mapped_column(
        SQLEnum(IntegrationStatus, values_callable=lambda x: [e.value for e in x]),
        default=IntegrationStatus.PENDING
    )
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    
    # Token management
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_refresh_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    
    # Health check
    last_health_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_health_status: Mapped[str | None] = mapped_column(
        String(20), nullable=True,
        comment="healthy|degraded|error"
    )
    
    # Provider-specific config
    provider_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict,
        comment="Provider-specific settings (repo list, project IDs, etc.)"
    )
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IntegrationCredential(Base):
    """Encrypted credentials for integrations."""
    
    __tablename__ = "integration_credentials"
    __table_args__ = (
        {"comment": "Encrypted tokens stored separately for security"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    integration_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("integrations.id", ondelete="CASCADE"), nullable=False
    )
    
    # Token types
    credential_type: Mapped[str] = mapped_column(
        String(50), nullable=False,
        comment="access_token|refresh_token|api_key|client_secret"
    )
    
    # Encrypted value (use pgsodium in production)
    encrypted_value: Mapped[str] = mapped_column(Text, nullable=False)
    
    # Metadata
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ═══════════════════════════════════════════════════════════════════════════════
# PATTERN DETECTION
# ═══════════════════════════════════════════════════════════════════════════════

class Pattern(Base):
    """Detected recurring workflow patterns."""
    
    __tablename__ = "patterns"
    __table_args__ = (
        Index("ix_patterns_workspace_suggested", "workspace_id", "is_suggested"),
        Index("ix_patterns_frequency", "frequency_score"),
        {"comment": "Detected recurring workflows for automation suggestions"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    channel_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("channels.id", ondelete="SET NULL"), nullable=True
    )
    
    # Pattern classification
    pattern_type: Mapped[str] = mapped_column(
        String(50), nullable=False,
        comment="report_request|integration_use|topic_discussion|custom"
    )
    topic: Mapped[str] = mapped_column(String(255), nullable=False)
    
    # Frequency analysis
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    frequency_score: Mapped[int] = mapped_column(Integer, default=0)
    
    # Who triggers this
    typical_requesters: Mapped[list[str]] = mapped_column(
        JSONB, default=list,
        comment="User IDs who commonly trigger this pattern"
    )
    
    # Suggestion workflow
    is_suggested: Mapped[bool] = mapped_column(Boolean, default=False)
    suggested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suggestion_response: Mapped[str | None] = mapped_column(
        String(20), nullable=True,
        comment="accepted|rejected|customize|pending"
    )
    
    # If accepted, link to schedule
    created_schedule_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("schedules.id", ondelete="SET NULL"), nullable=True
    )
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ═══════════════════════════════════════════════════════════════════════════════
# TIME-SERIES TABLES (High Volume, Partitioned)
# ═══════════════════════════════════════════════════════════════════════════════

class CostLog(Base):
    """Immutable cost tracking — partitioned by month."""
    
    __tablename__ = "cost_log"
    __table_args__ = (
        Index("ix_cost_log_workspace_month", "workspace_id", "year_month"),
        Index("ix_cost_log_task", "task_id"),
        Index("ix_cost_log_model", "model"),
        {"comment": "Time-series: per-call cost tracking (partitioned by month)"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    
    # What cost money
    component: Mapped[str] = mapped_column(
        String(50), nullable=False,
        comment="llm_call|tool_execution|integration_api|sandbox"
    )
    provider: Mapped[str] = mapped_column(
        String(50), nullable=False,
        comment="openrouter|composio|e2b|..."
    )
    model: Mapped[str | None] = mapped_column(
        String(100), nullable=True,
        comment="Model identifier for LLM calls"
    )
    
    # Usage
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    
    # Cost (USD)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    
    # Metadata
    request_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict,
        comment="Request/response metadata for debugging"
    )
    
    # Partitioning key
    year_month: Mapped[str] = mapped_column(
        String(7), nullable=False,
        comment="YYYY-MM for table partitioning"
    )
    
    # Timestamp (partition key)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuditLog(Base):
    """Immutable audit trail — partitioned by month."""
    
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_workspace_action", "workspace_id", "action"),
        Index("ix_audit_log_actor", "actor_id"),
        Index("ix_audit_log_target", "target_type", "target_id"),
        {"comment": "Time-series: audit trail (partitioned by month)"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    
    # Who did it
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_type: Mapped[str] = mapped_column(
        String(20), nullable=False,
        comment="user|system|agent|integration"
    )
    
    # What happened
    action: Mapped[str] = mapped_column(
        String(50), nullable=False,
        comment="task_created|approval_resolved|integration_connected|..."
    )
    
    # What was affected
    target_type: Mapped[str] = mapped_column(
        String(50), nullable=False,
        comment="task|integration|user|channel|..."
    )
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    
    # Context
    before_state: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True,
        comment="State before change (for updates)"
    )
    after_state: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True,
        comment="State after change"
    )
    
    # IP / request info
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    
    # Partitioning
    year_month: Mapped[str] = mapped_column(String(7), nullable=False)
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class WebhookDelivery(Base):
    """Incoming webhook delivery tracking — partitioned by month."""
    
    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        Index("ix_webhook_deliveries_workspace_source", "workspace_id", "source"),
        Index("ix_webhook_deliveries_status", "status"),
        {"comment": "Time-series: webhook delivery attempts"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    
    # Source
    source: Mapped[str] = mapped_column(
        String(50), nullable=False,
        comment="github|stripe|datadog|linear|..."
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    
    # Delivery
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    headers: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict)
    
    # Processing
    status: Mapped[str] = mapped_column(
        String(20), default="pending", nullable=False,
        comment="pending|processing|completed|failed|ignored"
    )
    processed_by: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
        comment="Which handler processed this"
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    
    # Retry tracking
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    
    # Partitioning
    year_month: Mapped[str] = mapped_column(String(7), nullable=False)
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ═══════════════════════════════════════════════════════════════════════════════
# SUPPORTING TABLES
# ═══════════════════════════════════════════════════════════════════════════════

class RateLimit(Base):
    """Token bucket rate limiting per workspace/user."""
    
    __tablename__ = "rate_limits"
    __table_args__ = (
        UniqueConstraint("scope_type", "scope_id", "limit_type", name="uix_rate_limit_scope"),
        {"comment": "Token bucket rate limiting"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    
    # Scope (workspace or user)
    scope_type: Mapped[str] = mapped_column(
        String(20), nullable=False,
        comment="workspace|user"
    )
    scope_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    
    # Limit type
    limit_type: Mapped[str] = mapped_column(
        String(50), nullable=False,
        comment="requests_per_minute|tokens_per_day|tasks_per_hour"
    )
    
    # Token bucket state
    tokens_remaining: Mapped[int] = mapped_column(Integer, nullable=False)
    tokens_max: Mapped[int] = mapped_column(Integer, nullable=False)
    last_refill_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    
    # Window tracking
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    window_requests: Mapped[int] = mapped_column(Integer, default=0)
    
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class FeatureFlag(Base):
    """Per-workspace feature flags for gradual rollout."""
    
    __tablename__ = "feature_flags"
    __table_args__ = (
        UniqueConstraint("workspace_id", "flag_name", name="uix_feature_flag"),
        {"comment": "Feature flags per workspace"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    
    flag_name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    
    # Gradual rollout
    rollout_percentage: Mapped[int] = mapped_column(Integer, default=100)
    
    # Config
    flag_config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ThreadConversation(Base):
    """Track active thread conversations where Lucy is participating.
    
    This enables Lucy to respond to follow-up messages in threads
    without requiring @mentions, while being smart about when to
    step back if the conversation shifts to other participants.
    """
    
    __tablename__ = "thread_conversations"
    __table_args__ = (
        UniqueConstraint("channel_id", "thread_ts", name="uix_thread_channel_thread"),
        Index("ix_threads_workspace_active", "workspace_id", "is_active"),
        Index("ix_threads_last_activity", "last_message_at"),
        {"comment": "Thread conversation tracking for smart auto-response"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    channel_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), nullable=False
    )
    
    # Slack identifiers
    slack_channel_id: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="Slack channel ID (C1234567890)"
    )
    thread_ts: Mapped[str] = mapped_column(
        String(50), nullable=False, comment="Thread parent timestamp"
    )
    
    # Who started the conversation
    initiator_user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    slack_initiator_id: Mapped[str] = mapped_column(
        String(32), nullable=False, comment="Slack user ID who started thread"
    )
    
    # Conversation state
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True,
        comment="Whether Lucy should auto-respond in this thread"
    )
    status: Mapped[str] = mapped_column(
        String(20), default="active", nullable=False,
        comment="active|paused|closed - Lucy auto-responds only when active"
    )
    
    # Participants tracking
    participant_slack_ids: Mapped[list[str]] = mapped_column(
        JSONB, default=list,
        comment="Slack user IDs who have participated in thread"
    )
    
    # Activity tracking
    message_count: Mapped[int] = mapped_column(Integer, default=1)
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lucy_last_responded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    
    # Auto-close settings
    auto_close_after_minutes: Mapped[int] = mapped_column(
        Integer, default=30,
        comment="Auto-close thread after this many minutes of inactivity"
    )
    
    # Context for smart detection
    last_intent: Mapped[str | None] = mapped_column(
        String(50), nullable=True,
        comment="Last classified intent in this thread"
    )
    conversation_summary: Mapped[str | None] = mapped_column(
        Text, nullable=True,
        comment="Brief summary of conversation for context"
    )
    
    # Related task
    last_task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    
    # Relationships
    workspace: Mapped[Workspace] = relationship()
    initiator: Mapped[User | None] = relationship()
