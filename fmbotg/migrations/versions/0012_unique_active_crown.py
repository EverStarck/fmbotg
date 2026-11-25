"""One active crown per artist per chat

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-23
"""
import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Two /whoknows runs racing could leave duplicates; keep the newest active crown of each
    op.execute("""
        UPDATE crowns SET active = false
        WHERE active AND id NOT IN (
            SELECT max(id) FROM crowns WHERE active GROUP BY chat_id, artist_key
        )
    """)
    op.create_index("ux_crowns_active_artist", "crowns", ["chat_id", "artist_key"], unique=True,
                    sqlite_where=sa.text("active"), postgresql_where=sa.text("active"))


def downgrade() -> None:
    op.drop_index("ux_crowns_active_artist", table_name="crowns")
