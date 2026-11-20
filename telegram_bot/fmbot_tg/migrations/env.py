import asyncio
import os

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from fmbot_tg.db import Base

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    return config.get_main_option("sqlalchemy.url") or os.getenv("DATABASE_URL", "sqlite+aiosqlite:///fmbot.db")


def _run(connection) -> None:
    # Batch mode lets ALTERs work on SQLite.
    context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


async def _run_online() -> None:
    engine = create_async_engine(_url())
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(_run_online())
