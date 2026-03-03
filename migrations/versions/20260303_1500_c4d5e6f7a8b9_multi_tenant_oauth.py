"""Add multi-tenant OAuth columns and auth tables.

Adds Workspace columns for encrypted bot token, team metadata,
agent email, and installation tracking. Creates oauth_states,
auth_codes, and background_tasks tables.

Revision ID: c4d5e6f7a8b9
Revises: b3f4e5a6d7c8
Create Date: 2026-03-03 15:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "c4d5e6f7a8b9"
down_revision = "b3f4e5a6d7c8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Workspace columns ────────────────────────────────────
    op.add_column("workspaces", sa.Column(
        "slack_bot_token_encrypted", sa.Text(), nullable=True,
        comment="Fernet-encrypted xoxb-... bot token",
    ))
    op.add_column("workspaces", sa.Column(
        "slack_bot_user_id", sa.String(32), nullable=True,
        comment="Lucy's bot user ID in this workspace (U...)",
    ))
    op.add_column("workspaces", sa.Column(
        "slack_team_name", sa.String(255), nullable=True,
        comment="Human-readable team name from Slack",
    ))
    op.add_column("workspaces", sa.Column(
        "slack_team_domain", sa.String(255), nullable=True,
        comment="Slack team domain slug",
    ))
    op.add_column("workspaces", sa.Column(
        "agent_email", sa.String(255), nullable=True,
        comment="Per-workspace agent email (e.g. table@zeeyamail.com)",
    ))
    op.add_column("workspaces", sa.Column(
        "owner_email", sa.String(255), nullable=True,
        comment="Email of the user who installed Lucy",
    ))
    op.add_column("workspaces", sa.Column(
        "installed_at", sa.DateTime(timezone=True), nullable=True,
    ))
    op.add_column("workspaces", sa.Column(
        "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true"),
        comment="False when uninstalled or token revoked",
    ))

    # ── OAuth states table ───────────────────────────────────
    op.create_table(
        "oauth_states",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("state", sa.String(128), unique=True, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        comment="CSRF state tokens for Slack OAuth (short-lived)",
    )
    op.create_index("ix_oauth_states_expires", "oauth_states", ["expires_at"])

    # ── Auth codes table ─────────────────────────────────────
    op.create_table(
        "auth_codes",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("code", sa.String(64), nullable=False, comment="6-digit OTP or URL-safe token"),
        sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True),
        sa.Column("is_used", sa.Boolean(), default=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        comment="One-time login codes sent via email",
    )
    op.create_index("ix_auth_codes_email_code", "auth_codes", ["email", "code"])
    op.create_index("ix_auth_codes_expires", "auth_codes", ["expires_at"])

    # ── Background tasks table ───────────────────────────────
    op.create_table(
        "background_tasks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("workspace_id", UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False),
        sa.Column("task_type", sa.String(50), nullable=False, comment="agent_run|scheduled_job|email_process"),
        sa.Column("payload", JSONB(), nullable=False, comment="Serialized inputs needed to resume/retry"),
        sa.Column("status", sa.String(20), nullable=False, server_default="running", comment="running|completed|interrupted|failed"),
        sa.Column("slack_channel_id", sa.String(32), nullable=True),
        sa.Column("slack_thread_ts", sa.String(50), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        comment="Persisted background tasks for restart resilience",
    )
    op.create_index("ix_bg_tasks_workspace_status", "background_tasks", ["workspace_id", "status"])


def downgrade() -> None:
    op.drop_table("background_tasks")
    op.drop_table("auth_codes")
    op.drop_table("oauth_states")

    op.drop_column("workspaces", "is_active")
    op.drop_column("workspaces", "installed_at")
    op.drop_column("workspaces", "owner_email")
    op.drop_column("workspaces", "agent_email")
    op.drop_column("workspaces", "slack_team_domain")
    op.drop_column("workspaces", "slack_team_name")
    op.drop_column("workspaces", "slack_bot_user_id")
    op.drop_column("workspaces", "slack_bot_token_encrypted")
