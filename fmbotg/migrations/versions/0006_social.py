"""Phase 4: privacy level, friends, crowns and chat settings

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-20
"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("privacy_level", sa.String(16), nullable=True))

    op.create_table(
        "friends",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("lastfm_username", sa.String(64), nullable=False),
        sa.Column("lastfm_key", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_friends_user_name", "friends", ["user_id", "lastfm_key"], unique=True)

    op.create_table(
        "chat_settings",
        sa.Column("chat_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("crown_threshold", sa.Integer(), nullable=True),
        sa.Column("crowns_disabled", sa.Boolean(), nullable=False),
    )

    op.create_table(
        "crowns",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("artist", sa.String(512), nullable=False),
        sa.Column("artist_key", sa.String(512), nullable=False),
        sa.Column("playcount", sa.Integer(), nullable=False),
        sa.Column("start_playcount", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("seeded", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_crowns_chat_artist", "crowns", ["chat_id", "artist_key", "active"])
    op.create_index("ix_crowns_chat_user", "crowns", ["chat_id", "user_id", "active"])


def downgrade() -> None:
    op.drop_table("crowns")
    op.drop_table("chat_settings")
    op.drop_table("friends")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("privacy_level")
