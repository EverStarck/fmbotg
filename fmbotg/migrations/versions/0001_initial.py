"""Phase 1 schema: users and chat members

Revision ID: 0001
Revises:
Create Date: 2026-11-10
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_username", sa.String(64), nullable=True),
        sa.Column("display_name", sa.String(128), nullable=True),
        sa.Column("lastfm_username", sa.String(64), nullable=True),
        sa.Column("session_key", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_telegram_id", "users", ["telegram_id"], unique=True)
    op.create_index("ix_users_telegram_username", "users", ["telegram_username"])

    op.create_table(
        "chat_members",
        sa.Column("chat_id", sa.BigInteger(), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), primary_key=True),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("chat_members")
    op.drop_index("ix_users_telegram_username", table_name="users")
    op.drop_index("ix_users_telegram_id", table_name="users")
    op.drop_table("users")
