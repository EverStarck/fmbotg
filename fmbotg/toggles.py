"""Per-group disabled commands (port of GuildDisabledCommandService / ChannelToggledCommandService).

Telegram groups have no channels, so commands are switched off for the whole group. A disabled
command is ignored silently, like FMBot does, so the bot doesn't add noise to the chat.
"""

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Router
from aiogram.filters import Command
from aiogram.types import Message, TelegramObject

from .cache import TTLCache
from .db import Database

# Never disabled, so admins can always undo it and people can always find help
PROTECTED = {"disable", "enable", "disabled", "help", "start", "login"}


class CommandToggles:
    def __init__(self, db: Database):
        self._db = db
        self._cache = TTLCache(ttl_seconds=300, max_items=20_000)
        self.canonical: dict[str, str] = {}  # any alias -> the command's main name

    def register(self, routers: list[Router]) -> None:
        for router in routers:
            for handler in router.message.handlers:
                for flt in handler.filters or []:
                    if isinstance(flt.callback, Command):
                        names = [c.lower() for c in flt.callback.commands if isinstance(c, str)]
                        for name in names:
                            self.canonical.setdefault(name, names[0])

    def resolve(self, name: str) -> str | None:
        return self.canonical.get(name.lstrip("/").split("@")[0].lower())

    async def disabled(self, chat_id: int) -> set[str]:
        cached = self._cache.get(chat_id)
        if cached is None:
            settings = await self._db.chat_settings(chat_id)
            cached = set((settings.disabled_commands or "").split())
            self._cache.set(chat_id, cached)
        return cached

    async def save(self, chat_id: int, commands: set[str]) -> None:
        await self._db.update_chat_settings(chat_id, disabled_commands=" ".join(sorted(commands)) or None)
        self._cache.set(chat_id, set(commands))


class DisabledCommandsMiddleware(BaseMiddleware):
    def __init__(self, toggles: CommandToggles):
        self._toggles = toggles

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # Commands also run from photo/video captions, so check those too
        text = (event.text or event.caption) if isinstance(event, Message) else None
        if isinstance(event, Message) and event.chat.type in ("group", "supergroup") and text and text.startswith("/"):
            command = self._toggles.resolve(text.split(maxsplit=1)[0])
            if command and command in await self._toggles.disabled(event.chat.id):
                return None
        return await handler(event, data)
