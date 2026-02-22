"""Initial schema: workspaces, users, tasks, approvals, integrations.

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2026-02-21 16:00:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# Revision identifiers
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create custom enums
    op.execute("""
        CREATE TYPE taskstatus AS ENUM (
            'created', 'pending_approval', 'running', 'completed', 
            'failed', 'cancelled', 'timeout'
        )
    """)
    op.execute("""
        CREATE TYPE taskpriority AS ENUM ('critical', 'high', 'normal', 'low', 'batch')
    """)
    op.execute("""
        CREATE TYPE approvalstatus AS ENUM (
            'pending', 'approved', 'rejected', 'expired'
        )
    """)
    op.execute("""
        CREATE TYPE integrationstatus AS ENUM (
            'pending', 'active', 'refreshing', 'error', 'revoked'
        )
    """)
    op.execute("""
        CREATE TYPE heartbeatstatus AS ENUM (
            'healthy', 'triggered', 'silenced', 'disabled'
        )
    """)

    # Workspaces
    op.create_table(
        "workspaces",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slack_team_id", sa.String(32), nullable=False, unique=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("domain", sa.String(255), nullable=True),
        sa.Column("plan", sa.String(20), nullable=False, server_default="starter"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("settings", postgresql.JSONB(), server_default="{}"),
        sa.Column("max_users", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("max_monthly_actions", sa.Integer(), nullable=False, server_default="500"),
        sa.Column("max_integrations", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("current_month_actions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_month_cost_usd", sa.Numeric(12, 4), nullable=False, server_default="0.0000"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_workspaces_slack_team_id", "workspaces", ["slack_team_id"], unique=True)
    op.create_index("ix_workspaces_status", "workspaces", ["status", "created_at"])

    # Users
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("slack_user_id", sa.String(32), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("avatar_url", sa.String(500), nullable=True),
        sa.Column("role", sa.String(20), nullable=False, server_default="member"),
        sa.Column("preferences", postgresql.JSONB(), server_default="{}"),
        sa.Column("has_personal_agent", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("personal_agent_config", postgresql.JSONB(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_unique_constraint("uix_user_workspace_slack", "users", ["workspace_id", "slack_user_id"])
    op.create_index("ix_users_workspace_role", "users", ["workspace_id", "role"])

    # Channels
    op.create_table(
        "channels",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("slack_channel_id", sa.String(32), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("channel_type", sa.String(20), nullable=False),
        sa.Column("lucy_joined_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_monitored", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("settings", postgresql.JSONB(), server_default="{}"),
        sa.Column("memory_scope_key", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_unique_constraint("uix_channel_workspace_slack", "channels", ["workspace_id", "slack_channel_id"])
    op.create_index("ix_channels_workspace_type", "channels", ["workspace_id", "channel_type"])

    # Agents
    op.create_table(
        "agents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_type", sa.String(20), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("config", postgresql.JSONB(), server_default="{}"),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("total_tasks_completed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_cost_usd", sa.Numeric(12, 4), nullable=False, server_default="0.0000"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_agents_workspace_type", "agents", ["workspace_id", "agent_type"])

    # Tasks
    op.create_table(
        "tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="SET NULL"), nullable=True),
        sa.Column("requester_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("channel_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("channels.id", ondelete="SET NULL"), nullable=True),
        sa.Column("slack_thread_ts", sa.String(50), nullable=True),
        sa.Column("intent", sa.String(100), nullable=True),
        sa.Column("priority", sa.SmallInteger(), nullable=False, server_default="2"),
        sa.Column("status", sa.Enum("taskstatus", name="taskstatus_enum"), nullable=False),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("config", postgresql.JSONB(), server_default="{}"),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("result_data", postgresql.JSONB(), nullable=True),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_tasks_workspace_status", "tasks", ["workspace_id", "status"])
    op.create_index("ix_tasks_workspace_created", "tasks", ["workspace_id", "created_at"])
    op.create_index("ix_tasks_requester", "tasks", ["requester_id", "created_at"])
    op.create_index("ix_tasks_agent_status", "tasks", ["agent_id", "status"])
    # Partial index for active tasks
    op.execute("""
        CREATE INDEX ix_tasks_active ON tasks (workspace_id, status)
        WHERE status IN ('created', 'pending_approval', 'running')
    """)

    # Task steps
    op.create_table(
        "task_steps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("step_type", sa.String(50), nullable=False),
        sa.Column("description", sa.String(500), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("config", postgresql.JSONB(), server_default="{}"),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_task_steps_task_seq", "task_steps", ["task_id", "sequence_number"])

    # Approvals
    op.create_table(
        "approvals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("approver_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("action_type", sa.String(50), nullable=False),
        sa.Column("action_description", sa.Text(), nullable=False),
        sa.Column("risk_level", sa.String(20), nullable=False, server_default="medium"),
        sa.Column("status", sa.Enum("approvalstatus", name="approvalstatus_enum"), nullable=False),
        sa.Column("slack_message_ts", sa.String(50), nullable=True),
        sa.Column("response", sa.Text(), nullable=True),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_approvals_workspace_status", "approvals", ["workspace_id", "status"])
    op.create_index("ix_approvals_approver", "approvals", ["approver_id", "status"])
    # Partial index for pending approvals
    op.execute("""
        CREATE INDEX ix_approvals_pending ON approvals (approver_id)
        WHERE status = 'pending'
    """)

    # Schedules
    op.create_table(
        "schedules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("cron_expression", sa.String(100), nullable=False),
        sa.Column("timezone", sa.String(50), nullable=False, server_default="UTC"),
        sa.Column("intent_template", sa.Text(), nullable=False),
        sa.Column("config", postgresql.JSONB(), server_default="{}"),
        sa.Column("target_channel_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("channels.id", ondelete="SET NULL"), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_task_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("run_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_schedules_workspace_active", "schedules", ["workspace_id", "is_active"])
    op.create_index("ix_schedules_next_run", "schedules", ["next_run_at"])

    # Heartbeats
    op.create_table(
        "heartbeats",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("condition_type", sa.String(50), nullable=False),
        sa.Column("condition_config", postgresql.JSONB(), nullable=False),
        sa.Column("check_interval_seconds", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("alert_channel_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("channels.id", ondelete="SET NULL"), nullable=True),
        sa.Column("alert_template", sa.Text(), nullable=False, server_default="Condition triggered: {name}"),
        sa.Column("alert_cooldown_seconds", sa.Integer(), nullable=False, server_default="3600"),
        sa.Column("last_alert_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("current_status", sa.Enum("heartbeatstatus", name="heartbeatstatus_enum"), nullable=False),
        sa.Column("check_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("trigger_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_check_result", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_heartbeats_workspace_active", "heartbeats", ["workspace_id", "is_active"])

    # Integrations
    op.create_table(
        "integrations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("external_account_id", sa.String(255), nullable=True),
        sa.Column("external_account_name", sa.String(255), nullable=True),
        sa.Column("scopes", postgresql.JSONB(), server_default="[]"),
        sa.Column("status", sa.Enum("integrationstatus", name="integrationstatus_enum"), nullable=False),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_refresh_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_health_check_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_health_status", sa.String(20), nullable=True),
        sa.Column("provider_config", postgresql.JSONB(), server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_integrations_workspace_provider", "integrations", ["workspace_id", "provider"])
    op.create_index("ix_integrations_status", "integrations", ["workspace_id", "status"])

    # Integration credentials (encrypted)
    op.create_table(
        "integration_credentials",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("integration_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("integrations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("credential_type", sa.String(50), nullable=False),
        sa.Column("encrypted_value", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # Patterns
    op.create_table(
        "patterns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("channel_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("channels.id", ondelete="SET NULL"), nullable=True),
        sa.Column("pattern_type", sa.String(50), nullable=False),
        sa.Column("topic", sa.String(255), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("frequency_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("typical_requesters", postgresql.JSONB(), server_default="[]"),
        sa.Column("is_suggested", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("suggested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("suggestion_response", sa.String(20), nullable=True),
        sa.Column("created_schedule_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("schedules.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_patterns_workspace_suggested", "patterns", ["workspace_id", "is_suggested"])
    op.create_index("ix_patterns_frequency", "patterns", ["frequency_score"])

    # Cost log (time-series, partitioned by month)
    op.create_table(
        "cost_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("component", sa.String(50), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("request_metadata", postgresql.JSONB(), server_default="{}"),
        sa.Column("year_month", sa.String(7), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_cost_log_workspace_month", "cost_log", ["workspace_id", "year_month"])
    op.create_index("ix_cost_log_task", "cost_log", ["task_id"])
    op.create_index("ix_cost_log_model", "cost_log", ["model"])

    # Audit log (time-series, partitioned by month)
    op.create_table(
        "audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("actor_type", sa.String(20), nullable=False),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("target_type", sa.String(50), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("before_state", postgresql.JSONB(), nullable=True),
        sa.Column("after_state", postgresql.JSONB(), nullable=True),
        sa.Column("ip_address", sa.String(45), nullable=True),
        sa.Column("user_agent", sa.String(500), nullable=True),
        sa.Column("year_month", sa.String(7), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_audit_log_workspace_action", "audit_log", ["workspace_id", "action"])
    op.create_index("ix_audit_log_actor", "audit_log", ["actor_id"])
    op.create_index("ix_audit_log_target", "audit_log", ["target_type", "target_id"])

    # Webhook deliveries (time-series, partitioned by month)
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("headers", postgresql.JSONB(), server_default="{}"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("processed_by", sa.String(50), nullable=True),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("year_month", sa.String(7), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_webhook_deliveries_workspace_source", "webhook_deliveries", ["workspace_id", "source"])
    op.create_index("ix_webhook_deliveries_status", "webhook_deliveries", ["status"])

    # Rate limits
    op.create_table(
        "rate_limits",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("scope_type", sa.String(20), nullable=False),
        sa.Column("scope_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("limit_type", sa.String(50), nullable=False),
        sa.Column("tokens_remaining", sa.Integer(), nullable=False),
        sa.Column("tokens_max", sa.Integer(), nullable=False),
        sa.Column("last_refill_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_requests", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint("uix_rate_limit_scope", "rate_limits", ["scope_type", "scope_id", "limit_type"])

    # Feature flags
    op.create_table(
        "feature_flags",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("flag_name", sa.String(100), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("rollout_percentage", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("flag_config", postgresql.JSONB(), server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint("uix_feature_flag", "feature_flags", ["workspace_id", "flag_name"])


def downgrade() -> None:
    # Drop tables in reverse order
    op.drop_table("feature_flags")
    op.drop_table("rate_limits")
    op.drop_table("webhook_deliveries")
    op.drop_table("audit_log")
    op.drop_table("cost_log")
    op.drop_table("patterns")
    op.drop_table("integration_credentials")
    op.drop_table("integrations")
    op.drop_table("heartbeats")
    op.drop_table("schedules")
    op.drop_table("approvals")
    op.drop_table("task_steps")
    op.drop_table("tasks")
    op.drop_table("agents")
    op.drop_table("channels")
    op.drop_table("users")
    op.drop_table("workspaces")

    # Drop custom types
    op.execute("DROP TYPE IF EXISTS taskstatus")
    op.execute("DROP TYPE IF EXISTS taskpriority")
    op.execute("DROP TYPE IF EXISTS approvalstatus")
    op.execute("DROP TYPE IF EXISTS integrationstatus")
    op.execute("DROP TYPE IF EXISTS heartbeatstatus")
