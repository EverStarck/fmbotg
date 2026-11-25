"""Engine and session factory, plus query helpers shared by the mixins."""

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from ..names import name_key
from .models import Base, SavedStreak, User, UserPlay


def _only(values: dict, allowed: set[str]) -> None:
    """Guard for methods that update a few named columns from **values."""
    unknown = set(values) - allowed
    if unknown:
        raise ValueError(f"Can't update {sorted(unknown)}; allowed: {sorted(allowed)}")


class _Base:
    def __init__(self, url: str):
        self.url = url
        self.engine: AsyncEngine = create_async_engine(url)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def create_schema(self) -> None:
        """For tests; the bot itself uses Alembic migrations (see migrate.py)."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        await self.engine.dispose()

    @staticmethod
    async def _drop_index(session, user: User, keep_streaks: bool = False) -> None:
        """Remove imported plays. Streaks go too, unless this is a re-import of the same account."""
        await session.execute(delete(UserPlay).where(UserPlay.user_id == user.id))
        if not keep_streaks:
            await session.execute(delete(SavedStreak).where(SavedStreak.user_id == user.id))
        user.indexed_at = None
        user.updated_at = None
        user.last_scrobble_ts = None

    def _subject_conditions(self, kind: str, subject: tuple[str, ...]) -> list:
        conditions = [UserPlay.artist_key == name_key(subject[0])]
        if kind == "album":
            conditions.append(UserPlay.album_key == name_key(subject[1]))
        elif kind == "track":
            conditions.append(UserPlay.track_key == name_key(subject[1]))
        return conditions

    def _kind_columns(self, kind: str):
        """(group-by key columns, display name column) for artists/albums/tracks."""
        return {
            "artists": ([UserPlay.artist_key], UserPlay.artist),
            "albums": ([UserPlay.artist_key, UserPlay.album_key], UserPlay.album),
            "tracks": ([UserPlay.artist_key, UserPlay.track_key], UserPlay.track),
        }[kind]
