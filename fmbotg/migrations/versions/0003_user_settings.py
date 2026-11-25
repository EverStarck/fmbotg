"""Phase 3: user settings

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-20
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("timezone", sa.String(64), nullable=True))
        batch.add_column(sa.Column("fm_mode", sa.String(16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("fm_mode")
        batch.drop_column("timezone")
