"""Phase 3: saved streaks

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-20
"""
import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_streaks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("artist", sa.String(512), nullable=False),
        sa.Column("album", sa.String(512), nullable=True),
        sa.Column("track", sa.String(512), nullable=False),
        sa.Column("artist_count", sa.Integer(), nullable=False),
        sa.Column("album_count", sa.Integer(), nullable=False),
        sa.Column("track_count", sa.Integer(), nullable=False),
        sa.Column("started", sa.BigInteger(), nullable=False),
        sa.Column("ended", sa.BigInteger(), nullable=False),
    )
    op.create_index("ix_user_streaks_user_started", "user_streaks", ["user_id", "started"], unique=True)


def downgrade() -> None:
    op.drop_table("user_streaks")
