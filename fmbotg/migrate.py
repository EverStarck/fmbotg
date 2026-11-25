"""Run Alembic migrations at startup. Call before the event loop starts (Alembic's env uses asyncio.run)."""

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

MIGRATIONS = Path(__file__).parent / "migrations"


def alembic_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


async def _table_names(database_url: str) -> set[str]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as conn:
            return set(await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names()))
    finally:
        await engine.dispose()


def upgrade(database_url: str) -> None:
    config = alembic_config(database_url)
    tables = asyncio.run(_table_names(database_url))
    if "users" in tables and "alembic_version" not in tables:
        # Database created by phase 1's create_all, which matches revision 0001.
        command.stamp(config, "0001")
    command.upgrade(config, "head")
