"""Phase 4: artist genres and countries

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-20
"""
import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "artist_metadata",
        sa.Column("artist_key", sa.String(512), primary_key=True),
        sa.Column("artist", sa.String(512), nullable=False),
        sa.Column("tags", sa.String(1000), nullable=True),
        sa.Column("tags_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("country_code", sa.String(8), nullable=True),
        sa.Column("country", sa.String(128), nullable=True),
        sa.Column("country_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("artist_metadata")
