"""Phase 5: owner moderation, censored music, album release dates

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-23
"""
import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("bot_banned", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("global_hidden", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "censored_music",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("artist", sa.String(512), nullable=False),
        sa.Column("album", sa.String(512), nullable=True),
        sa.Column("artist_key", sa.String(512), nullable=False),
        sa.Column("album_key", sa.String(512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_censored_music_key", "censored_music", ["artist_key", "album_key"], unique=True)
    op.create_table(
        "album_metadata",
        sa.Column("artist_key", sa.String(512), primary_key=True),
        sa.Column("album_key", sa.String(512), primary_key=True),
        sa.Column("release_date", sa.String(10), nullable=False),
        sa.Column("looked_up_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("album_metadata")
    op.drop_table("censored_music")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("global_hidden")
        batch.drop_column("bot_banned")
