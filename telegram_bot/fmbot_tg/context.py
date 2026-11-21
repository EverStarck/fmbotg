"""Resolving who a command is about, and what artist/album/track it refers to.

Telegram equivalent of FMBot's ContextModel + SettingService.GetUser: a command targets the
caller by default, the author of the replied-to message, an @handle of a registered user,
or a raw Last.fm name written as `lfm:name`.
"""

from dataclasses import dataclass, field
from datetime import timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram.types import Message

from .db import Database, User
from .lastfm import LastFmClient


class UserFacingError(Exception):
    """Raised from handlers; the message is sent to the user as-is (HTML)."""


class NotRegistered(UserFacingError):
    pass


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
    def from_user(cls, user: User, is_self: bool) -> "Target":
        return cls(user.lastfm_username, user.session_key, is_self, user.id, user.indexed_at is not None,
                   load_timezone(user.timezone), user.fm_mode or "full", user.fm_template)


def load_timezone(name: str | None) -> tzinfo:
    if not name:
        return timezone.utc
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


def _target_from_user(user: User | None, is_self: bool, who: str) -> Target:
    if user is None or not user.lastfm_username:
        raise NotRegistered(NOT_REGISTERED_SELF if is_self else f"{who} hasn't connected Last.fm to this bot.")
    return Target.from_user(user, is_self)


async def resolve_target(message: Message, args: list[str], db: Database) -> tuple[Target, list[str]]:
    for index, arg in enumerate(args):
        rest = args[:index] + args[index + 1:]
        if arg.lower().startswith("lfm:") and len(arg) > 4:
            return Target(arg[4:], None, False), rest
        if arg.startswith("@") and len(arg) > 1:
            return _target_from_user(await db.get_user_by_handle(arg), False, arg), rest

    reply = message.reply_to_message
    if reply and reply.from_user and not reply.from_user.is_bot and reply.from_user.id != message.from_user.id:
        user = await db.get_user(reply.from_user.id)
        return _target_from_user(user, False, reply.from_user.full_name), list(args)

    return _target_from_user(await db.get_user(message.from_user.id), True, ""), list(args)


async def resolve_subject(
    kind: str, query: str, target: Target | None, lastfm: LastFmClient
) -> tuple[str, ...]:
    """Returns (artist,) or (artist, title). Empty query means the target's current track."""
    query = query.strip()
    if not query:
        if target is None:
            raise UserFacingError(f"Tell me which {kind} to look up.")
        recent = await lastfm.recent_tracks(target.lastfm_username, limit=1, session_key=target.session_key)
        if not recent.tracks:
            raise UserFacingError("No scrobbles found to look up. Add a search, e.g. <code>/artist Radiohead</code>.")
        scrobble = recent.tracks[0]
        if kind == "artist":
            return (scrobble.artist,)
        if kind == "album":
            if not scrobble.album:
                raise UserFacingError("The current track has no album.")
            return scrobble.artist, scrobble.album
        return scrobble.artist, scrobble.track

    if kind == "artist":
        return (query,)

    if " - " in query:
        artist, title = query.split(" - ", 1)
        return artist.strip(), title.strip()

    found = await (lastfm.search_album(query) if kind == "album" else lastfm.search_track(query))
    if not found:
        raise UserFacingError(f"Couldn't find a {kind} matching that. Try <code>Artist - Title</code>.")
    return found
