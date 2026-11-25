"""Resolving who a command is about, and what artist/album/track it refers to.

Telegram equivalent of FMBot's ContextModel + SettingService.GetUser: a command targets the
caller by default, the author of the replied-to message, an @handle of a registered user,
or a raw Last.fm name written as `lfm:name`.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from .db import Database, User
from .domain import NOT_REGISTERED_SELF, NotRegistered, Target, UserFacingError
from .formatting import esc
from .lastfm import LastFmClient


def _target_from_user(user: User | None, is_self: bool, who: str) -> Target:
    if user is None or not user.lastfm_username:
        # `who` is a Telegram name or a typed @handle: escape it, replies are HTML
        raise NotRegistered(NOT_REGISTERED_SELF if is_self else f"{esc(who)} hasn't connected Last.fm to this bot.")
    return Target.from_user(user, is_self)


class Aliases:
    """Per-chat nicknames from FM_ALIASES in .env: chat id -> {nickname: Last.fm username}."""

    def __init__(self, by_chat: dict[int, dict[str, str]] | None = None):
        self._by_chat = {chat: {nick.lower(): user for nick, user in names.items()}
                         for chat, names in (by_chat or {}).items()}

    def apply(self, chat_id: int, args: list[str], search: bool = False) -> list[str]:
        """Replace a nickname with `lfm:<username>`.

        Without a search, any word can be a nickname. Commands that also take a search ("/artist red hot
        chili peppers") only check the last word, so names at the start of a search aren't taken as people.
        """
        names = self._by_chat.get(chat_id)
        if not names or not args:
            return list(args)
        for index in ([len(args) - 1] if search else range(len(args))):
            nickname = args[index].lower()
            if nickname in names:
                return args[:index] + [f"lfm:{names[nickname]}"] + args[index + 1:]
        return list(args)


# The dispatcher's nicknames while it handles an update (set by AliasMiddleware), so the many
# resolve_target callers don't each need them passed in, and two dispatchers never share them.
_current_aliases: ContextVar[Aliases] = ContextVar("aliases", default=Aliases())


@contextmanager
def use_aliases(aliases: Aliases):
    token = _current_aliases.set(aliases)
    try:
        yield
    finally:
        _current_aliases.reset(token)


class AliasMiddleware(BaseMiddleware):
    def __init__(self, aliases: Aliases):
        self._aliases = aliases

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        with use_aliases(self._aliases):
            return await handler(event, data)


def apply_alias(chat_id: int, args: list[str], search: bool = False) -> list[str]:
    return _current_aliases.get().apply(chat_id, args, search)


async def resolve_target(message: Message, args: list[str], db: Database,
                         search: bool = False) -> tuple[Target, list[str]]:
    """Who the command is about. Set `search=True` when the remaining args are a music search."""
    args = apply_alias(message.chat.id, args, search)
    for index, arg in enumerate(args):
        rest = args[:index] + args[index + 1:]
        if arg.lower().startswith("lfm:") and len(arg) > 4:
            name = arg[4:]
            # Someone who connected Last.fm to the bot gets their index and settings too
            user = await db.get_user_by_lastfm(name)
            return (Target.from_user(user, False) if user else Target(name, None, False)), rest
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


async def self_user(message: Message, db: Database) -> User:
    """The caller's account, for commands that are always about yourself."""
    user = await db.get_user(message.from_user.id)
    if user is None or not user.lastfm_username:
        raise NotRegistered(NOT_REGISTERED_SELF)
    return user


async def self_target(message: Message, db: Database) -> Target:
    """The caller as a Target, for commands that are always about yourself."""
    return Target.from_user(await self_user(message, db), True)


def chat_title(message: Message, fallback: str = "this chat") -> str:
    """The group's name for headings (unescaped)."""
    return message.chat.title or fallback


def require_group(message: Message, what: str = "This command") -> None:
    if message.chat.type not in ("group", "supergroup"):
        raise UserFacingError(f"{what} only works in groups.")


async def require_group_admin(message: Message) -> None:
    """Port of FMBot's guild permission checks: only group admins change group settings."""
    require_group(message)
    if message.sender_chat is not None and message.sender_chat.id == message.chat.id:
        return  # an admin posting anonymously as the group
    member = await message.bot.get_chat_member(message.chat.id, message.from_user.id)
    if member.status not in ("creator", "administrator"):
        raise UserFacingError("Only group admins can do that.")
