from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import (
    BigInteger, DateTime, ForeignKey, Index, Integer, String, delete, func, insert, or_, select, update,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# SQLite only auto-increments INTEGER PRIMARY KEY columns.
BigIntPK = BigInteger().with_variant(Integer, "sqlite")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def name_key(value: str | None) -> str:
    """Case-insensitive match key for artist/album/track names (portable replacement for citext)."""
    return (value or "").strip().lower()[:512]


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    telegram_username: Mapped[str | None] = mapped_column(String(64), index=True)
    display_name: Mapped[str | None] = mapped_column(String(128))
    lastfm_username: Mapped[str | None] = mapped_column(String(64))
    session_key: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Play index state. indexed_at is set once a full history import has completed.
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Last import or update attempt, successful or not.
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_scrobble_ts: Mapped[int | None] = mapped_column(BigInteger)


class ChatMember(Base):
    """Telegram has no API to list group members, so we remember who we've seen in each chat."""

    __tablename__ = "chat_members"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UserPlay(Base):
    """One scrobble (port of FMBot's user_plays)."""

    __tablename__ = "user_plays"
    __table_args__ = (
        Index("ix_user_plays_user_time", "user_id", "played_at"),
        Index("ix_user_plays_artist_user", "artist_key", "user_id"),
        Index("ix_user_plays_album", "artist_key", "album_key", "user_id"),
        Index("ix_user_plays_track", "artist_key", "track_key", "user_id"),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    played_at: Mapped[int] = mapped_column(BigInteger)  # unix seconds
    artist: Mapped[str] = mapped_column(String(512))
    album: Mapped[str | None] = mapped_column(String(512))
    track: Mapped[str] = mapped_column(String(512))
    artist_key: Mapped[str] = mapped_column(String(512))
    album_key: Mapped[str | None] = mapped_column(String(512))
    track_key: Mapped[str] = mapped_column(String(512))


@dataclass(frozen=True)
class PlayRow:
    played_at: int
    artist: str
    album: str | None
    track: str

    def as_insert(self, user_id: int) -> dict:
        return {
            "user_id": user_id,
            "played_at": self.played_at,
            "artist": self.artist[:512],
            "album": (self.album or None) and self.album[:512],
            "track": self.track[:512],
            "artist_key": name_key(self.artist),
            "album_key": name_key(self.album) or None,
            "track_key": name_key(self.track),
        }


@dataclass(frozen=True)
class AggregateRow:
    name: str
    artist: str | None
    playcount: int


class Database:
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

    # --- users --------------------------------------------------------------------------

    async def get_user(self, telegram_id: int) -> User | None:
        async with self.sessions() as session:
            return await session.scalar(select(User).where(User.telegram_id == telegram_id))

    async def get_user_by_id(self, user_id: int) -> User | None:
        async with self.sessions() as session:
            return await session.get(User, user_id)

    async def get_user_by_handle(self, handle: str) -> User | None:
        async with self.sessions() as session:
            return await session.scalar(
                select(User).where(func.lower(User.telegram_username) == handle.lstrip("@").lower())
            )

    async def get_user_by_lastfm(self, lastfm_username: str) -> User | None:
        async with self.sessions() as session:
            return await session.scalar(
                select(User).where(func.lower(User.lastfm_username) == lastfm_username.lower()).limit(1)
            )

    async def link_lastfm(self, telegram_id: int, lastfm_username: str, session_key: str) -> User:
        """Links an account. Switching to a different Last.fm account drops the old play index."""
        async with self.sessions() as session:
            user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
            if user is None:
                user = User(telegram_id=telegram_id)
                session.add(user)
                await session.flush()
            elif (user.lastfm_username or "").lower() != lastfm_username.lower():
                await self._drop_index(session, user)
            user.lastfm_username = lastfm_username
            user.session_key = session_key
            user.last_used = utcnow()
            await session.commit()
            return user

    async def unlink_lastfm(self, telegram_id: int) -> bool:
        async with self.sessions() as session:
            user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
            if user is None or user.lastfm_username is None:
                return False
            await self._drop_index(session, user)
            user.lastfm_username = None
            user.session_key = None
            await session.commit()
            return True

    @staticmethod
    async def _drop_index(session, user: User) -> None:
        await session.execute(delete(UserPlay).where(UserPlay.user_id == user.id))
        user.indexed_at = None
        user.updated_at = None
        user.last_scrobble_ts = None

    async def touch(self, telegram_id: int, handle: str | None, display_name: str, chat_id: int | None) -> None:
        """Record that a Telegram user was seen, optionally in a group chat."""
        async with self.sessions() as session:
            user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
            if user is None:
                user = User(telegram_id=telegram_id)
                session.add(user)
            user.telegram_username = handle
            user.display_name = display_name[:128]
            user.last_used = utcnow()

            if chat_id is not None:
                member = await session.get(ChatMember, (chat_id, telegram_id))
                if member is None:
                    session.add(ChatMember(chat_id=chat_id, telegram_id=telegram_id))
                else:
                    member.last_seen = utcnow()
            try:
                await session.commit()
            except IntegrityError:
                # Two updates for a new user raced; the other one won and that's fine.
                await session.rollback()

    async def remove_member(self, chat_id: int, telegram_id: int) -> None:
        async with self.sessions() as session:
            member = await session.get(ChatMember, (chat_id, telegram_id))
            if member is not None:
                await session.delete(member)
                await session.commit()

    async def chat_lastfm_users(self, chat_id: int, limit: int) -> list[User]:
        async with self.sessions() as session:
            rows = await session.scalars(
                select(User)
                .join(ChatMember, ChatMember.telegram_id == User.telegram_id)
                .where(ChatMember.chat_id == chat_id, User.lastfm_username.is_not(None))
                .order_by(ChatMember.last_seen.desc())
                .limit(limit)
            )
            return list(rows)

    # --- play index ---------------------------------------------------------------------

    async def begin_full_index(self, user_id: int) -> None:
        async with self.sessions() as session:
            user = await session.get(User, user_id)
            await self._drop_index(session, user)
            user.updated_at = utcnow()
            await session.commit()

    async def insert_plays(self, user_id: int, plays: list[PlayRow]) -> None:
        if not plays:
            return
        async with self.sessions() as session:
            await session.execute(insert(UserPlay), [p.as_insert(user_id) for p in plays])
            await session.commit()

    async def finish_index(self, user_id: int, last_scrobble_ts: int, full: bool) -> None:
        values = {"updated_at": utcnow(), "last_scrobble_ts": last_scrobble_ts}
        if full:
            values["indexed_at"] = utcnow()
        async with self.sessions() as session:
            await session.execute(update(User).where(User.id == user_id).values(**values))
            await session.commit()

    async def mark_attempt(self, user_id: int) -> None:
        async with self.sessions() as session:
            await session.execute(update(User).where(User.id == user_id).values(updated_at=utcnow()))
            await session.commit()

    async def users_due_for_update(self, limit: int, stale_after: timedelta,
                                   active_within: timedelta) -> list[User]:
        """Indexed users who used the bot recently and haven't been updated for a while."""
        now = utcnow()
        async with self.sessions() as session:
            rows = await session.scalars(
                select(User)
                .where(User.lastfm_username.is_not(None), User.indexed_at.is_not(None),
                       User.last_used > now - active_within, User.updated_at < now - stale_after)
                .order_by(User.updated_at)
                .limit(limit)
            )
            return list(rows)

    async def users_needing_import(self, limit: int, retry_after: timedelta) -> list[User]:
        """Linked users without a completed import, e.g. after a restart lost the queue."""
        async with self.sessions() as session:
            rows = await session.scalars(
                select(User)
                .where(User.lastfm_username.is_not(None), User.indexed_at.is_(None),
                       or_(User.updated_at.is_(None), User.updated_at < utcnow() - retry_after))
                .limit(limit)
            )
            return list(rows)

    async def play_count(self, user_id: int) -> int:
        async with self.sessions() as session:
            return await session.scalar(select(func.count()).where(UserPlay.user_id == user_id)) or 0

    async def playcounts(self, kind: str, subject: tuple[str, ...], user_ids: list[int]) -> dict[int, int]:
        """Per-user playcount for an artist, album or track among `user_ids` (who knows)."""
        if not user_ids:
            return {}
        conditions = [UserPlay.artist_key == name_key(subject[0]), UserPlay.user_id.in_(user_ids)]
        if kind == "album":
            conditions.append(UserPlay.album_key == name_key(subject[1]))
        elif kind == "track":
            conditions.append(UserPlay.track_key == name_key(subject[1]))
        async with self.sessions() as session:
            rows = await session.execute(
                select(UserPlay.user_id, func.count()).where(*conditions).group_by(UserPlay.user_id)
            )
            return {user_id: count for user_id, count in rows}

    async def top_from_plays(self, user_id: int, kind: str, from_ts: int, to_ts: int,
                             limit: int = 1000) -> list[AggregateRow]:
        """Top artists/albums/tracks in [from_ts, to_ts). Names are matched case-insensitively."""
        key_columns = {
            "artists": [UserPlay.artist_key],
            "albums": [UserPlay.artist_key, UserPlay.album_key],
            "tracks": [UserPlay.artist_key, UserPlay.track_key],
        }[kind]
        name_column = {"artists": UserPlay.artist, "albums": UserPlay.album, "tracks": UserPlay.track}[kind]
        conditions = [UserPlay.user_id == user_id, UserPlay.played_at >= from_ts, UserPlay.played_at < to_ts]
        if kind == "albums":
            conditions.append(UserPlay.album_key.is_not(None))

        playcount = func.count().label("playcount")
        stmt = (
            select(func.max(name_column), func.max(UserPlay.artist), playcount)
            .where(*conditions)
            .group_by(*key_columns)
            .order_by(playcount.desc(), func.max(name_column))
            .limit(limit)
        )
        async with self.sessions() as session:
            rows = await session.execute(stmt)
            return [AggregateRow(name, artist if kind != "artists" else None, count)
                    for name, artist, count in rows]
