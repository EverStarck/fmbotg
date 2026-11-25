"""Phase 5: artist photos and listeners, track lengths

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-21
"""
import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("artist_metadata") as batch:
        batch.add_column(sa.Column("image_url", sa.String(512), nullable=True))
        batch.add_column(sa.Column("image_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("listeners", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("listeners_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "track_metadata",
        sa.Column("artist_key", sa.String(512), primary_key=True),
        sa.Column("track_key", sa.String(512), primary_key=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("deezer_url", sa.String(256), nullable=True),
        sa.Column("looked_up_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("track_metadata")
    with op.batch_alter_table("artist_metadata") as batch:
        for column in ("listeners_at", "listeners", "image_at", "image_url"):
            batch.drop_column(column)
