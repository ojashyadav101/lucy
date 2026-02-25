"""Add email_address to agents table.

Revision ID: b3f4e5a6d7c8
Revises: a1b2c3d4e5f6
Create Date: 2025-02-24 22:00:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "b3f4e5a6d7c8"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column(
            "email_address",
            sa.String(length=255),
            nullable=True,
            comment="Agent's own email address (e.g. lucy@ziamail.com)",
        ),
    )


def downgrade() -> None:
    op.drop_column("agents", "email_address")
