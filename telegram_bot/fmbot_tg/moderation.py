"""Owner moderation (port of AdminService's global block list, botted users and CensorService).

Banned people are ignored entirely; censored artists and albums never have their images shown
(charts, /fm covers, pixelation). The sets live in memory, loaded at startup and updated by the
owner commands, so checks cost nothing per message.
"""

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from .db import Database, name_key

_owners: set[int] = set()
_banned: set[int] = set()
_censored: set[tuple[str, str]] = set()  # (artist key, album key or "" for the whole artist)


def configure_owners(owner_ids: set[int]) -> None:
    _owners.clear()
    _owners.update(owner_ids)


async def load(db: Database) -> None:
    _banned.clear()
    _banned.update(await db.banned_telegram_ids())
    _censored.clear()
    _censored.update((c.artist_key, c.album_key) for c in await db.censored())


def is_owner(telegram_id: int) -> bool:
    return telegram_id in _owners


def set_banned(telegram_id: int, banned: bool) -> None:
    (_banned.add if banned else _banned.discard)(telegram_id)


def set_censored(artist: str, album: str | None, censored: bool) -> None:
    key = (name_key(artist), name_key(album))
    (_censored.add if censored else _censored.discard)(key)


def is_censored(artist: str | None, album: str | None = None) -> bool:
    artist_key = name_key(artist)
    return (artist_key, "") in _censored or (album is not None and (artist_key, name_key(album)) in _censored)


class BanMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = event.from_user if isinstance(event, (Message, CallbackQuery)) else None
        if user is not None and user.id in _banned and user.id not in _owners:
            return None
        return await handler(event, data)
