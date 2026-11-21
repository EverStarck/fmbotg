"""Phase 3: /fm templates

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-20
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("fm_template", sa.String(2000), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("fm_template")
