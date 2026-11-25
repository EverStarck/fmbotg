"""Phase 2: play index

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-10
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("last_scrobble_ts", sa.BigInteger(), nullable=True))

    op.create_table(
        "user_plays",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("played_at", sa.BigInteger(), nullable=False),
        sa.Column("artist", sa.String(512), nullable=False),
        sa.Column("album", sa.String(512), nullable=True),
        sa.Column("track", sa.String(512), nullable=False),
        sa.Column("artist_key", sa.String(512), nullable=False),
        sa.Column("album_key", sa.String(512), nullable=True),
        sa.Column("track_key", sa.String(512), nullable=False),
    )
    op.create_index("ix_user_plays_user_time", "user_plays", ["user_id", "played_at"])
    op.create_index("ix_user_plays_artist_user", "user_plays", ["artist_key", "user_id"])
    op.create_index("ix_user_plays_album", "user_plays", ["artist_key", "album_key", "user_id"])
    op.create_index("ix_user_plays_track", "user_plays", ["artist_key", "track_key", "user_id"])


def downgrade() -> None:
    op.drop_table("user_plays")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("last_scrobble_ts")
        batch.drop_column("updated_at")
        batch.drop_column("indexed_at")
