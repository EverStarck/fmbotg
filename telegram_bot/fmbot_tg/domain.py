"""Core types shared by services and handlers, free of Telegram and database code."""

from dataclasses import dataclass, field
from datetime import datetime, timezone, tzinfo
from typing import TYPE_CHECKING, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

if TYPE_CHECKING:
    from .db import User


# Two spellings on purpose: a subject is one artist/album/track; top lists are of artists/albums/tracks.
SubjectKind = Literal["artist", "album", "track"]
TopKind = Literal["artists", "albums", "tracks"]
_TOP_KIND: dict[str, TopKind] = {"artist": "artists", "album": "albums", "track": "tracks"}


def top_kind(kind: SubjectKind) -> TopKind:
    return _TOP_KIND[kind]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    """SQLite returns stored UTC datetimes without a zone; Postgres keeps it."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class UserFacingError(Exception):
    """Raised from handlers; the message is sent to the user as-is (HTML)."""


class NotRegistered(UserFacingError):
    pass


STALE_BUTTON = "This message is too old for its buttons. Run the command again."


NOT_REGISTERED_SELF = (
    "You haven't connected your Last.fm account yet.\n"
    "Send /login to me in a private chat to get started."
)


@dataclass(frozen=True)
class Target:
    lastfm_username: str
    session_key: str | None
    is_self: bool
    user_id: int | None = None  # set for registered users
    indexed: bool = False  # full scrobble history is in the play index
    tz: tzinfo = field(default=timezone.utc)
    fm_mode: str = "full"
    fm_template: str | None = None

    @classmethod
    def from_user(cls, user: "User", is_self: bool) -> "Target":
        return cls(user.lastfm_username, user.session_key, is_self, user.id, user.indexed_at is not None,
                   load_timezone(user.timezone), user.fm_mode or "full", user.fm_template)


def load_timezone(name: str | None) -> tzinfo:
    if not name:
        return timezone.utc
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


