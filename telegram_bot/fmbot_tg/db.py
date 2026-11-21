from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, delete, func, insert, or_, select, update,
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
    # Settings (port of the user settings FMBot keeps on its users table)
    timezone: Mapped[str | None] = mapped_column(String(64))  # IANA name, None = UTC
    fm_mode: Mapped[str | None] = mapped_column(String(16))  # "full" (default), "compact" or "template"
    fm_template: Mapped[str | None] = mapped_column(String(2000))
    # "global" shows your name in global who knows; anything else (default) shows "Private user"
    privacy_level: Mapped[str | None] = mapped_column(String(16))


PRIVACY_GLOBAL = "global"
PRIVACY_CHAT = "chat"


class Friend(Base):
    """A Last.fm user someone added with /addfriend (port of FMBot's friends table)."""

    __tablename__ = "friends"
    __table_args__ = (Index("ix_friends_user_name", "user_id", "lastfm_key", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    lastfm_username: Mapped[str] = mapped_column(String(64))
    lastfm_key: Mapped[str] = mapped_column(String(64))  # lowercase, for case-insensitive uniqueness
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ChatSettings(Base):
    """Per-group settings (port of the crown columns on FMBot's guilds table)."""

    __tablename__ = "chat_settings"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    crown_threshold: Mapped[int | None] = mapped_column(Integer)  # None = DEFAULT_CROWN_THRESHOLD
    crowns_disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    disabled_commands: Mapped[str | None] = mapped_column(String(2000))  # space-separated canonical names


class ChatBlockedUser(Base):
    """Someone an admin left out of this group's leaderboards (port of FMBot's guild blocked users)."""

    __tablename__ = "chat_blocked_users"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


DEFAULT_CROWN_THRESHOLD = 30


class UserShortcut(Base):
    """`/name` that runs another command, e.g. /mine -> /fm lfm:me (port of FMBot's user_shortcuts)."""

    __tablename__ = "user_shortcuts"
    __table_args__ = (Index("ix_user_shortcuts_user_name", "user_id", "name", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(32))  # lowercase, without the slash
    output: Mapped[str] = mapped_column(String(500))  # the command it runs, with its slash


class Autopost(Base):
    """A group chart posted on a schedule (port of FMBot's guild autoposts)."""

    __tablename__ = "autoposts"

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    content: Mapped[str] = mapped_column(String(16))  # "recap", "artists", "albums" or "tracks"
    schedule: Mapped[str] = mapped_column(String(16))  # "weekly" or "monthly"
    all_time: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    next_post_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ArtistMeta(Base):
    """Genres (Last.fm tags) and country (MusicBrainz) per artist, cached across users
    (port of the genre and country columns FMBot keeps on its artists table)."""

    __tablename__ = "artist_metadata"

    artist_key: Mapped[str] = mapped_column(String(512), primary_key=True)
    artist: Mapped[str] = mapped_column(String(512))
    tags: Mapped[str | None] = mapped_column(String(1000))  # "|"-separated genres; "" = looked up, none found
    tags_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    country_code: Mapped[str | None] = mapped_column(String(8))  # ISO 3166-1; "" = looked up, unknown
    country: Mapped[str | None] = mapped_column(String(128))
    country_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Crown(Base):
    """Who has the most plays of an artist in a group (port of FMBot's user_crowns).

    A stolen crown is kept with active=False so /crown can show its history.
    """

    __tablename__ = "crowns"
    __table_args__ = (
        Index("ix_crowns_chat_artist", "chat_id", "artist_key", "active"),
        Index("ix_crowns_chat_user", "chat_id", "user_id", "active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    artist: Mapped[str] = mapped_column(String(512))
    artist_key: Mapped[str] = mapped_column(String(512))
    playcount: Mapped[int] = mapped_column(Integer)
    start_playcount: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    seeded: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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


class SavedStreak(Base):
    """A streak worth remembering (port of FMBot's user_streaks; saved at 25+ plays)."""

    __tablename__ = "user_streaks"
    __table_args__ = (Index("ix_user_streaks_user_started", "user_id", "started", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    artist: Mapped[str] = mapped_column(String(512))
    album: Mapped[str | None] = mapped_column(String(512))
    track: Mapped[str] = mapped_column(String(512))
    artist_count: Mapped[int] = mapped_column(Integer)
    album_count: Mapped[int] = mapped_column(Integer)
    track_count: Mapped[int] = mapped_column(Integer)
    started: Mapped[int] = mapped_column(BigInteger)
    ended: Mapped[int] = mapped_column(BigInteger)


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
    async def _drop_index(session, user: User, keep_streaks: bool = False) -> None:
        """Remove imported plays. Streaks go too, unless this is a re-import of the same account."""
        await session.execute(delete(UserPlay).where(UserPlay.user_id == user.id))
        if not keep_streaks:
            await session.execute(delete(SavedStreak).where(SavedStreak.user_id == user.id))
        user.indexed_at = None
        user.updated_at = None
        user.last_scrobble_ts = None

    async def update_settings(self, telegram_id: int, **values) -> None:
        allowed = {"timezone", "fm_mode", "fm_template", "privacy_level"}
        assert set(values) <= allowed, values
        async with self.sessions() as session:
            await session.execute(update(User).where(User.telegram_id == telegram_id).values(**values))
            await session.commit()

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
        """Linked members seen in this chat, most recently active first, without blocked ones."""
        blocked = select(ChatBlockedUser.user_id).where(ChatBlockedUser.chat_id == chat_id)
        async with self.sessions() as session:
            rows = await session.scalars(
                select(User)
                .join(ChatMember, ChatMember.telegram_id == User.telegram_id)
                .where(ChatMember.chat_id == chat_id, User.lastfm_username.is_not(None), User.id.not_in(blocked))
                .order_by(ChatMember.last_seen.desc())
                .limit(limit)
            )
            return list(rows)

    # --- play index ---------------------------------------------------------------------

    async def begin_full_index(self, user_id: int) -> None:
        async with self.sessions() as session:
            user = await session.get(User, user_id)
            await self._drop_index(session, user, keep_streaks=True)
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
        conditions = [UserPlay.user_id.in_(user_ids), *self._subject_conditions(kind, subject)]
        async with self.sessions() as session:
            rows = await session.execute(
                select(UserPlay.user_id, func.count()).where(*conditions).group_by(UserPlay.user_id)
            )
            return {user_id: count for user_id, count in rows}

    async def top_from_plays(self, user_id: int, kind: str, from_ts: int, to_ts: int,
                             limit: int = 1000, artist: str | None = None) -> list[AggregateRow]:
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
        if artist is not None:
            conditions.append(UserPlay.artist_key == name_key(artist))

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

    def _subject_conditions(self, kind: str, subject: tuple[str, ...]) -> list:
        conditions = [UserPlay.artist_key == name_key(subject[0])]
        if kind == "album":
            conditions.append(UserPlay.album_key == name_key(subject[1]))
        elif kind == "track":
            conditions.append(UserPlay.track_key == name_key(subject[1]))
        return conditions

    async def subject_stats(self, user_id: int, kind: str, subject: tuple[str, ...],
                            from_ts: int = 0, to_ts: int = 2**62) -> tuple[int, int | None, int | None]:
        """(playcount, first play, last play) of an artist/album/track for one user."""
        stmt = select(func.count(), func.min(UserPlay.played_at), func.max(UserPlay.played_at)).where(
            UserPlay.user_id == user_id, UserPlay.played_at >= from_ts, UserPlay.played_at < to_ts,
            *self._subject_conditions(kind, subject),
        )
        async with self.sessions() as session:
            count, first, last = (await session.execute(stmt)).one()
            return count, first, last

    async def recent_plays(self, user_id: int, before_ts: int, limit: int) -> list[UserPlay]:
        """Newest first, strictly before `before_ts`."""
        async with self.sessions() as session:
            rows = await session.scalars(
                select(UserPlay)
                .where(UserPlay.user_id == user_id, UserPlay.played_at < before_ts)
                .order_by(UserPlay.played_at.desc(), UserPlay.id.desc())
                .limit(limit)
            )
            return list(rows)

    async def plays_between(self, user_id: int, from_ts: int, to_ts: int) -> list[UserPlay]:
        async with self.sessions() as session:
            rows = await session.scalars(
                select(UserPlay)
                .where(UserPlay.user_id == user_id, UserPlay.played_at >= from_ts, UserPlay.played_at < to_ts)
                .order_by(UserPlay.played_at)
            )
            return list(rows)

    async def count_plays(self, user_id: int, from_ts: int, to_ts: int) -> int:
        async with self.sessions() as session:
            return await session.scalar(
                select(func.count()).where(UserPlay.user_id == user_id, UserPlay.played_at >= from_ts,
                                           UserPlay.played_at < to_ts)
            ) or 0

    async def discoveries(self, user_id: int, from_ts: int, to_ts: int, limit: int = 1000) -> list[tuple[str, int, int]]:
        """Artists first played in [from_ts, to_ts): (artist, first play, all-time plays), most played first."""
        first = func.min(UserPlay.played_at)
        plays = func.count().label("plays")
        stmt = (
            select(func.max(UserPlay.artist), first, plays)
            .where(UserPlay.user_id == user_id)
            .group_by(UserPlay.artist_key)
            .having(first >= from_ts, first < to_ts)
            .order_by(plays.desc(), first)
            .limit(limit)
        )
        async with self.sessions() as session:
            return [(artist, ts, count) for artist, ts, count in await session.execute(stmt)]

    async def track_counts(self, user_id: int, artist: str, tracks: list[str]) -> dict[str, int]:
        """Plays per track name (match key) for one artist, regardless of album."""
        keys = [name_key(t) for t in tracks]
        async with self.sessions() as session:
            rows = await session.execute(
                select(UserPlay.track_key, func.count())
                .where(UserPlay.user_id == user_id, UserPlay.artist_key == name_key(artist),
                       UserPlay.track_key.in_(keys))
                .group_by(UserPlay.track_key)
            )
            return {key: count for key, count in rows}

    # --- phase 3: gaps, search, overview, streaks -------------------------------------------

    def _kind_columns(self, kind: str):
        """(group-by key columns, display name column) for artists/albums/tracks."""
        return {
            "artists": ([UserPlay.artist_key], UserPlay.artist),
            "albums": ([UserPlay.artist_key, UserPlay.album_key], UserPlay.album),
            "tracks": ([UserPlay.artist_key, UserPlay.track_key], UserPlay.track),
        }[kind]

    async def listening_gaps(self, user_id: int, kind: str, min_plays: int,
                             min_gap_seconds: int) -> list[tuple[str, str, int, int, int]]:
        """Things you came back to after a long break (port of PlayBuilders.ListeningGapsAsync).

        Returns (name, artist, resumed at, gap seconds, total plays) using the first long gap of each,
        longest gaps first.
        """
        keys, name_col = self._kind_columns(kind)
        previous = func.lag(UserPlay.played_at).over(partition_by=keys, order_by=UserPlay.played_at)
        total = func.count().over(partition_by=keys)
        conditions = [UserPlay.user_id == user_id]
        if kind == "albums":
            conditions.append(UserPlay.album_key.is_not(None))
        inner = (
            select(*[k.label(f"k{i}") for i, k in enumerate(keys)], name_col.label("name"),
                   UserPlay.artist.label("artist"), UserPlay.played_at.label("played_at"),
                   previous.label("previous"), total.label("total"))
            .where(*conditions)
            .subquery()
        )
        stmt = (
            select(inner)
            .where(inner.c.previous.is_not(None), inner.c.played_at - inner.c.previous >= min_gap_seconds,
                   inner.c.total >= min_plays)
            .order_by(inner.c.played_at)
        )
        seen: set[tuple] = set()
        gaps = []
        async with self.sessions() as session:
            for row in await session.execute(stmt):
                key = tuple(row[i] for i in range(len(keys)))
                if key in seen:
                    continue  # FMBot reports the first long gap per item
                seen.add(key)
                gaps.append((row.name, row.artist, row.played_at, row.played_at - row.previous, row.total))
        gaps.sort(key=lambda g: -g[3])
        return gaps

    async def search_library(self, user_id: int, kind: str, text: str, limit: int = 10) -> list[AggregateRow]:
        """Your artists/albums/tracks whose name contains `text`, most played first."""
        keys, name_col = self._kind_columns(kind)
        key_col = keys[-1]
        playcount = func.count().label("playcount")
        stmt = (
            select(func.max(name_col), func.max(UserPlay.artist), playcount)
            .where(UserPlay.user_id == user_id, key_col.contains(name_key(text), autoescape=True))
            .group_by(*keys)
            .order_by(playcount.desc())
            .limit(limit)
        )
        async with self.sessions() as session:
            return [AggregateRow(n, a if kind != "artists" else None, c) for n, a, c in await session.execute(stmt)]

    async def subject_timestamps(self, user_id: int, kind: str, subject: tuple[str, ...]) -> list[int]:
        async with self.sessions() as session:
            rows = await session.scalars(
                select(UserPlay.played_at).where(UserPlay.user_id == user_id, *self._subject_conditions(kind, subject))
            )
            return list(rows)

    async def save_streak(self, user_id: int, streak: "SavedStreak") -> bool:
        """Insert, or update the saved streak with the same start. Returns True if it was new."""
        async with self.sessions() as session:
            existing = await session.scalar(select(SavedStreak).where(
                SavedStreak.user_id == user_id, SavedStreak.started == streak.started))
            if existing is None:
                streak.user_id = user_id
                session.add(streak)
            else:
                for column in ("artist", "album", "track", "artist_count", "album_count", "track_count", "ended"):
                    setattr(existing, column, getattr(streak, column))
            await session.commit()
            return existing is None

    async def saved_streaks(self, user_id: int, limit: int = 15) -> list[SavedStreak]:
        async with self.sessions() as session:
            rows = await session.scalars(
                select(SavedStreak).where(SavedStreak.user_id == user_id).order_by(SavedStreak.started.desc()).limit(limit)
            )
            return list(rows)

    # --- phase 4: global who knows, friends, crowns ----------------------------------------

    async def global_playcounts(self, kind: str, subject: tuple[str, ...], limit: int = 100) -> list[tuple[User, int]]:
        """Top listeners of an artist/album/track among everyone with an imported history."""
        plays = func.count().label("plays")
        stmt = (
            select(User, plays)
            .join(UserPlay, UserPlay.user_id == User.id)
            .where(User.indexed_at.is_not(None), *self._subject_conditions(kind, subject))
            .group_by(User.id)
            .order_by(plays.desc(), User.id)
            .limit(limit)
        )
        async with self.sessions() as session:
            return [(user, count) for user, count in await session.execute(stmt)]

    async def friends(self, user_id: int) -> list[str]:
        async with self.sessions() as session:
            rows = await session.scalars(
                select(Friend.lastfm_username).where(Friend.user_id == user_id).order_by(Friend.created_at))
            return list(rows)

    async def add_friend(self, user_id: int, lastfm_username: str) -> bool:
        """False if they were already a friend."""
        async with self.sessions() as session:
            exists = await session.scalar(select(Friend.id).where(
                Friend.user_id == user_id, Friend.lastfm_key == lastfm_username.lower()))
            if exists is not None:
                return False
            session.add(Friend(user_id=user_id, lastfm_username=lastfm_username, lastfm_key=lastfm_username.lower()))
            await session.commit()
            return True

    async def remove_friend(self, user_id: int, lastfm_username: str) -> bool:
        async with self.sessions() as session:
            result = await session.execute(delete(Friend).where(
                Friend.user_id == user_id, Friend.lastfm_key == lastfm_username.lower()))
            await session.commit()
            return result.rowcount > 0

    async def users_by_lastfm(self, names: list[str]) -> dict[str, User]:
        """Registered users by lowercase Last.fm name."""
        if not names:
            return {}
        async with self.sessions() as session:
            rows = await session.scalars(
                select(User).where(func.lower(User.lastfm_username).in_([n.lower() for n in names])))
            return {u.lastfm_username.lower(): u for u in rows}

    async def chat_settings(self, chat_id: int) -> ChatSettings:
        async with self.sessions() as session:
            return await session.get(ChatSettings, chat_id) or ChatSettings(chat_id=chat_id, crowns_disabled=False)

    async def update_chat_settings(self, chat_id: int, **values) -> None:
        assert set(values) <= {"crown_threshold", "crowns_disabled", "disabled_commands"}, values
        async with self.sessions() as session:
            settings = await session.get(ChatSettings, chat_id)
            if settings is None:
                settings = ChatSettings(chat_id=chat_id, crowns_disabled=False)
                session.add(settings)
            for key, value in values.items():
                setattr(settings, key, value)
            await session.commit()

    async def active_crown(self, chat_id: int, artist: str) -> Crown | None:
        async with self.sessions() as session:
            return await session.scalar(select(Crown).where(
                Crown.chat_id == chat_id, Crown.artist_key == name_key(artist), Crown.active.is_(True)))

    async def crown_history(self, chat_id: int, artist: str, limit: int = 10) -> list[tuple[Crown, User]]:
        async with self.sessions() as session:
            rows = await session.execute(
                select(Crown, User).join(User, User.id == Crown.user_id)
                .where(Crown.chat_id == chat_id, Crown.artist_key == name_key(artist))
                .order_by(Crown.created_at.desc(), Crown.id.desc()).limit(limit))
            return [(crown, user) for crown, user in rows]

    async def set_crown(self, chat_id: int, artist: str, user_id: int, playcount: int,
                        seeded: bool = False) -> Crown:
        """Give the artist's crown in this chat to `user_id`, or update the holder's playcount."""
        async with self.sessions() as session:
            current = await session.scalar(select(Crown).where(
                Crown.chat_id == chat_id, Crown.artist_key == name_key(artist), Crown.active.is_(True)))
            if current is not None and current.user_id == user_id:
                current.playcount = playcount
                current.updated_at = utcnow()
                crown = current
            else:
                if current is not None:
                    current.active = False
                    current.updated_at = utcnow()
                crown = Crown(chat_id=chat_id, user_id=user_id, artist=artist[:512], artist_key=name_key(artist),
                              playcount=playcount, start_playcount=playcount, active=True, seeded=seeded)
                session.add(crown)
            await session.commit()
            return crown

    async def update_crown_playcount(self, crown_id: int, playcount: int) -> None:
        async with self.sessions() as session:
            await session.execute(update(Crown).where(Crown.id == crown_id).values(
                playcount=playcount, updated_at=utcnow()))
            await session.commit()

    async def delete_crowns(self, chat_id: int, artist: str | None = None) -> int:
        """Remove crowns (all history) for one artist, or for the whole chat."""
        conditions = [Crown.chat_id == chat_id]
        if artist is not None:
            conditions.append(Crown.artist_key == name_key(artist))
        async with self.sessions() as session:
            result = await session.execute(delete(Crown).where(*conditions))
            await session.commit()
            return result.rowcount

    async def user_crowns(self, chat_id: int, user_id: int) -> list[Crown]:
        async with self.sessions() as session:
            rows = await session.scalars(select(Crown).where(
                Crown.chat_id == chat_id, Crown.user_id == user_id, Crown.active.is_(True))
                .order_by(Crown.playcount.desc()))
            return list(rows)

    async def crown_leaderboard(self, chat_id: int, user_ids: list[int], limit: int = 15) -> list[tuple[User, int]]:
        crowns = func.count().label("crowns")
        async with self.sessions() as session:
            rows = await session.execute(
                select(User, crowns).join(Crown, Crown.user_id == User.id)
                .where(Crown.chat_id == chat_id, Crown.active.is_(True), User.id.in_(user_ids))
                .group_by(User.id).order_by(crowns.desc(), User.id).limit(limit))
            return [(user, count) for user, count in rows]

    async def crown_candidates(self, user_ids: list[int], threshold: int) -> list[tuple[str, int, int]]:
        """(artist, user id, plays) for every artist someone has at least `threshold` plays of."""
        if not user_ids:
            return []
        plays = func.count().label("plays")
        stmt = (
            select(func.max(UserPlay.artist), UserPlay.user_id, plays)
            .where(UserPlay.user_id.in_(user_ids))
            .group_by(UserPlay.artist_key, UserPlay.user_id)
            .having(plays >= threshold)
        )
        async with self.sessions() as session:
            return [(artist, user_id, count) for artist, user_id, count in await session.execute(stmt)]

    async def seed_crowns(self, chat_id: int, picks: list[tuple[str, int, int]]) -> int:
        """Create crowns from (artist, user id, plays) for artists that have no active crown yet."""
        async with self.sessions() as session:
            taken = set(await session.scalars(select(Crown.artist_key).where(
                Crown.chat_id == chat_id, Crown.active.is_(True))))
            now = utcnow()
            rows = [{"chat_id": chat_id, "user_id": user_id, "artist": artist[:512], "artist_key": name_key(artist),
                     "playcount": plays, "start_playcount": plays, "active": True, "seeded": True,
                     "created_at": now, "updated_at": now}
                    for artist, user_id, plays in picks if name_key(artist) not in taken]
            if rows:
                await session.execute(insert(Crown), rows)
            await session.commit()
            return len(rows)

    # --- phase 4: artist genres and countries -------------------------------------------------

    async def artist_meta(self, keys: list[str]) -> dict[str, ArtistMeta]:
        found: dict[str, ArtistMeta] = {}
        async with self.sessions() as session:
            for start in range(0, len(keys), 500):
                rows = await session.scalars(select(ArtistMeta).where(ArtistMeta.artist_key.in_(keys[start:start + 500])))
                found.update({row.artist_key: row for row in rows})
        return found

    async def save_artist_meta(self, artist: str, **values) -> None:
        """Upsert tags/tags_at or country_code/country/country_at for one artist."""
        assert set(values) <= {"tags", "tags_at", "country_code", "country", "country_at"}, values
        for attempt in range(2):
            async with self.sessions() as session:
                row = await session.get(ArtistMeta, name_key(artist))
                if row is None:
                    row = ArtistMeta(artist_key=name_key(artist), artist=artist[:512])
                    session.add(row)
                for key, value in values.items():
                    setattr(row, key, value)
                try:
                    await session.commit()
                    return
                except IntegrityError:
                    # The tag and country lookups for a new artist raced; update the other one's row
                    await session.rollback()
                    if attempt:
                        raise

    # --- phase 4: group administration ------------------------------------------------------

    async def block_user(self, chat_id: int, user_id: int) -> bool:
        async with self.sessions() as session:
            if await session.get(ChatBlockedUser, (chat_id, user_id)) is not None:
                return False
            session.add(ChatBlockedUser(chat_id=chat_id, user_id=user_id))
            await session.commit()
            return True

    async def unblock_user(self, chat_id: int, user_id: int) -> bool:
        async with self.sessions() as session:
            result = await session.execute(delete(ChatBlockedUser).where(
                ChatBlockedUser.chat_id == chat_id, ChatBlockedUser.user_id == user_id))
            await session.commit()
            return result.rowcount > 0

    async def blocked_users(self, chat_id: int) -> list[User]:
        async with self.sessions() as session:
            rows = await session.scalars(select(User).join(ChatBlockedUser, ChatBlockedUser.user_id == User.id)
                                         .where(ChatBlockedUser.chat_id == chat_id).order_by(ChatBlockedUser.created_at))
            return list(rows)

    async def group_top(self, user_ids: list[int], kind: str, from_ts: int, to_ts: int,
                        limit: int = 100) -> list[tuple[str, str | None, int, int]]:
        """(name, artist, plays, listeners) across several people's play index (server top lists)."""
        if not user_ids:
            return []
        keys, name_col = self._kind_columns(kind)
        conditions = [UserPlay.user_id.in_(user_ids), UserPlay.played_at >= from_ts, UserPlay.played_at < to_ts]
        if kind == "albums":
            conditions.append(UserPlay.album_key.is_not(None))
        plays = func.count().label("plays")
        stmt = (
            select(func.max(name_col), func.max(UserPlay.artist), plays, func.count(UserPlay.user_id.distinct()))
            .where(*conditions)
            .group_by(*keys)
            .order_by(plays.desc(), func.max(name_col))
            .limit(limit)
        )
        async with self.sessions() as session:
            return [(name, artist if kind != "artists" else None, count, listeners)
                    for name, artist, count, listeners in await session.execute(stmt)]

    # --- phase 4: shortcuts and autoposts ---------------------------------------------------

    async def shortcuts(self, telegram_id: int) -> dict[str, str]:
        async with self.sessions() as session:
            rows = await session.execute(
                select(UserShortcut.name, UserShortcut.output)
                .join(User, User.id == UserShortcut.user_id).where(User.telegram_id == telegram_id)
                .order_by(UserShortcut.name))
            return {name: output for name, output in rows}

    async def set_shortcut(self, user_id: int, name: str, output: str) -> bool:
        """Add or replace. Returns True if it was new."""
        async with self.sessions() as session:
            existing = await session.scalar(select(UserShortcut).where(
                UserShortcut.user_id == user_id, UserShortcut.name == name))
            if existing is None:
                session.add(UserShortcut(user_id=user_id, name=name, output=output))
            else:
                existing.output = output
            await session.commit()
            return existing is None

    async def remove_shortcut(self, user_id: int, name: str) -> bool:
        async with self.sessions() as session:
            result = await session.execute(delete(UserShortcut).where(
                UserShortcut.user_id == user_id, UserShortcut.name == name))
            await session.commit()
            return result.rowcount > 0

    async def autoposts(self, chat_id: int) -> list[Autopost]:
        async with self.sessions() as session:
            return list(await session.scalars(select(Autopost).where(Autopost.chat_id == chat_id).order_by(Autopost.id)))

    async def add_autopost(self, chat_id: int, content: str, schedule: str, all_time: bool,
                           next_post_at: datetime) -> Autopost:
        async with self.sessions() as session:
            post = Autopost(chat_id=chat_id, content=content, schedule=schedule, all_time=all_time, enabled=True,
                            next_post_at=next_post_at)
            session.add(post)
            await session.commit()
            return post

    async def update_autopost(self, autopost_id: int, **values) -> None:
        assert set(values) <= {"enabled", "next_post_at", "last_posted_at"}, values
        async with self.sessions() as session:
            await session.execute(update(Autopost).where(Autopost.id == autopost_id).values(**values))
            await session.commit()

    async def remove_autopost(self, chat_id: int, autopost_id: int) -> bool:
        async with self.sessions() as session:
            result = await session.execute(delete(Autopost).where(
                Autopost.chat_id == chat_id, Autopost.id == autopost_id))
            await session.commit()
            return result.rowcount > 0

    async def due_autoposts(self, now: datetime, limit: int = 20) -> list[Autopost]:
        async with self.sessions() as session:
            return list(await session.scalars(
                select(Autopost).where(Autopost.enabled.is_(True), Autopost.next_post_at <= now)
                .order_by(Autopost.next_post_at).limit(limit)))
