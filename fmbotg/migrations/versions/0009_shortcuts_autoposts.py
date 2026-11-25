"""Phase 4: user shortcuts and group autoposts

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-20
"""
import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_shortcuts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(32), nullable=False),
        sa.Column("output", sa.String(500), nullable=False),
    )
    op.create_index("ix_user_shortcuts_user_name", "user_shortcuts", ["user_id", "name"], unique=True)

    op.create_table(
        "autoposts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("content", sa.String(16), nullable=False),
        sa.Column("schedule", sa.String(16), nullable=False),
        sa.Column("all_time", sa.Boolean(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("next_post_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_autoposts_chat_id", "autoposts", ["chat_id"])
    op.create_index("ix_autoposts_next_post_at", "autoposts", ["next_post_at"])


def downgrade() -> None:
    op.drop_table("autoposts")
    op.drop_table("user_shortcuts")
