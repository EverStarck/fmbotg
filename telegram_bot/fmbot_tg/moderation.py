"""Owner moderation (port of AdminService's global block list, botted users and CensorService).

Banned people are ignored entirely; censored artists and albums never have their images shown
(charts, /fm covers, /album, pixelation). Bans and censoring are kept in memory, loaded at
startup, so checking them costs nothing per message; every change goes through this class,
which writes the database and the memory copy together.
"""

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from .db import Database, User
from .names import name_key


class Moderation:
    def __init__(self, db: Database, owner_ids: set[int] | frozenset[int]):
        self._db = db
        self._owners = frozenset(owner_ids)
        self._banned: set[int] = set()
        self._censored: set[tuple[str, str]] = set()  # (artist key, album key or "" for the whole artist)

    async def load(self) -> None:
        self._banned = await self._db.banned_telegram_ids()
        self._censored = {(c.artist_key, c.album_key) for c in await self._db.censored()}

    def is_owner(self, telegram_id: int) -> bool:
        return telegram_id in self._owners

    def is_banned(self, telegram_id: int) -> bool:
        return telegram_id in self._banned and telegram_id not in self._owners

    def is_censored(self, artist: str | None, album: str | None = None) -> bool:
        artist_key = name_key(artist)
        return (artist_key, "") in self._censored or (album is not None and (artist_key, name_key(album)) in self._censored)

    async def set_banned(self, user: User, banned: bool) -> None:
        await self._db.set_user_flag(user.id, bot_banned=banned)
        (self._banned.add if banned else self._banned.discard)(user.telegram_id)

    async def set_hidden(self, user: User, hidden: bool) -> None:
        """Hidden people are left out of global who knows (FMBot's botted users)."""
        await self._db.set_user_flag(user.id, global_hidden=hidden)

    async def censor(self, artist: str, album: str | None) -> bool:
        """False if it was already censored."""
        if not await self._db.add_censored(artist, album):
            return False
        self._censored.add((name_key(artist), name_key(album)))
        return True

    async def uncensor(self, artist: str, album: str | None) -> bool:
        if not await self._db.remove_censored(artist, album):
            return False
        self._censored.discard((name_key(artist), name_key(album)))
        return True


class BanMiddleware(BaseMiddleware):
    """Drops every message and button press from banned people before anything else runs."""

    def __init__(self, moderation: Moderation):
        self._moderation = moderation

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = event.from_user if isinstance(event, (Message, CallbackQuery)) else None
        if user is not None and self._moderation.is_banned(user.id):
            return None
        return await handler(event, data)
