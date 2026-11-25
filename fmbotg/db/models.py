"""Tables (SQLAlchemy models). Migrations in fmbotg/migrations must match these."""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ..domain import utcnow

# SQLite only auto-increments INTEGER PRIMARY KEY columns.
BigIntPK = BigInteger().with_variant(Integer, "sqlite")


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
    # Owner moderation (port of FMBot's global block list and botted users)
    bot_banned: Mapped[bool] = mapped_column(Boolean, default=False)  # the bot ignores them entirely
    global_hidden: Mapped[bool] = mapped_column(Boolean, default=False)  # left out of global who knows

    @property
    def shown_name(self) -> str:
        """Telegram name, else Last.fm name, for leaderboards."""
        return self.display_name or self.lastfm_username or ""


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
    image_url: Mapped[str | None] = mapped_column(String(512))  # Deezer artist photo; "" = none found
    image_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    listeners: Mapped[int | None] = mapped_column(BigInteger)  # Last.fm listeners, the iceberg's popularity
    listeners_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CensoredMusic(Base):
    """Artists or albums whose images are never shown (port of CensorService's censored music)."""

    __tablename__ = "censored_music"
    __table_args__ = (Index("ix_censored_music_key", "artist_key", "album_key", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    artist: Mapped[str] = mapped_column(String(512))
    album: Mapped[str | None] = mapped_column(String(512))  # None = every album of the artist
    artist_key: Mapped[str] = mapped_column(String(512))
    album_key: Mapped[str] = mapped_column(String(512))  # "" for the whole artist
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AlbumMeta(Base):
    """First release date per album from MusicBrainz (port of the release dates FMBot shows)."""

    __tablename__ = "album_metadata"

    artist_key: Mapped[str] = mapped_column(String(512), primary_key=True)
    album_key: Mapped[str] = mapped_column(String(512), primary_key=True)
    release_date: Mapped[str] = mapped_column(String(10))  # YYYY, YYYY-MM or YYYY-MM-DD; "" = unknown
    looked_up_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TrackMeta(Base):
    """Track length and Deezer link, cached across users (port of the track lengths FMBot gets from Spotify)."""

    __tablename__ = "track_metadata"

    artist_key: Mapped[str] = mapped_column(String(512), primary_key=True)
    track_key: Mapped[str] = mapped_column(String(512), primary_key=True)
    duration_ms: Mapped[int] = mapped_column(Integer)  # 0 = unknown
    deezer_url: Mapped[str | None] = mapped_column(String(256))
    looked_up_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Crown(Base):
    """Who has the most plays of an artist in a group (port of FMBot's user_crowns).

    A stolen crown is kept with active=False so /crown can show its history.
    """

    __tablename__ = "crowns"
    __table_args__ = (
        Index("ix_crowns_chat_artist", "chat_id", "artist_key", "active"),
        Index("ix_crowns_chat_user", "chat_id", "user_id", "active"),
        # One active crown per artist per chat, even if two /whoknows runs race
        Index("ux_crowns_active_artist", "chat_id", "artist_key", unique=True,
              sqlite_where=text("active"), postgresql_where=text("active")),
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
