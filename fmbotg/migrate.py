"""Run Alembic migrations at startup. Call before the event loop starts (Alembic's env uses asyncio.run)."""

import asyncio
import logging
import time
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, InterfaceError
from sqlalchemy.ext.asyncio import create_async_engine

MIGRATIONS = Path(__file__).parent / "migrations"


def alembic_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


async def _table_names(database_url: str) -> set[str]:
    # Fail fast on an unreachable host (asyncpg waits 60s by default); the caller retries
    connect_args = {"timeout": 10} if make_url(database_url).drivername.endswith("asyncpg") else {}
    engine = create_async_engine(database_url, connect_args=connect_args)
    try:
        async with engine.connect() as conn:
            return set(await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names()))
    finally:
        await engine.dispose()


log = logging.getLogger(__name__)


def _reason(exc: BaseException) -> str:
    text = str(exc).strip().splitlines()
    return f"{type(exc).__name__}: {text[0]}" if text else type(exc).__name__


def _wait_for_tables(database_url: str, attempts: int, delay: float) -> set[str]:
    """The database's tables, retrying while it starts up (a fresh Postgres container takes a few seconds).
    Gives up with a short message, without the password, instead of a long traceback."""
    where = make_url(database_url).render_as_string(hide_password=True)
    for attempt in range(1, attempts + 1):
        try:
            return asyncio.run(_table_names(database_url))
        except (OSError, DBAPIError, InterfaceError) as exc:
            reason = _reason(exc.orig if isinstance(exc, DBAPIError) and exc.orig else exc)
            if attempt == attempts:
                raise SystemExit(f"Could not connect to the database at {where} ({reason})") from None
            log.warning("Database not reachable yet (%s), retrying in %ss", reason, delay)
            time.sleep(delay)
    raise AssertionError("unreachable")


def upgrade(database_url: str, attempts: int = 10, delay: float = 3) -> None:
    config = alembic_config(database_url)
    tables = _wait_for_tables(database_url, attempts, delay)
    if "users" in tables and "alembic_version" not in tables:
        # Database created by phase 1's create_all, which matches revision 0001.
        command.stamp(config, "0001")
    command.upgrade(config, "head")
