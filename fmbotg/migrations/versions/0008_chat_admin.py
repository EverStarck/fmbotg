"""Phase 4: disabled commands and blocked users per chat

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-20
"""
import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("chat_settings") as batch:
        batch.add_column(sa.Column("disabled_commands", sa.String(2000), nullable=True))
    op.create_table(
        "chat_blocked_users",
        sa.Column("chat_id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("chat_blocked_users")
    with op.batch_alter_table("chat_settings") as batch:
        batch.drop_column("disabled_commands")
