"""Per-user command shortcuts (port of ShortcutService): `/mine` can run `/fm lfm:someone`.

Shortcuts are expanded before anything else sees the message, so disabled commands, nicknames
and arguments all work on the expanded command. A real command name always wins over a shortcut.
"""

import re
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from .cache import TTLCache
from .db import Database
from .toggles import CommandToggles

MAX_SHORTCUTS = 20
NAME_RE = re.compile(r"^[a-z0-9_]{1,32}$")  # what Telegram allows in a command


class Shortcuts:
    def __init__(self, db: Database, toggles: CommandToggles):
        self._db = db
        self.toggles = toggles
        self._cache = TTLCache(ttl_seconds=600, max_items=50_000)

    async def for_user(self, telegram_id: int) -> dict[str, str]:
        cached = self._cache.get(telegram_id)
        if cached is None:
            cached = await self._db.shortcuts(telegram_id)
            self._cache.set(telegram_id, cached)
        return cached

    def forget(self, telegram_id: int) -> None:
        self._cache.set(telegram_id, None)

    async def expand(self, telegram_id: int, text: str) -> str | None:
        """The command a message stands for, or None if it isn't one of the user's shortcuts."""
        if not text.startswith("/"):
            return None
        first, _, rest = text.partition(" ")
        name = first[1:].split("@")[0].lower()
        if self.toggles.resolve(name) is not None:
            return None
        output = (await self.for_user(telegram_id)).get(name)
        if output is None:
            return None
        return output + (" " + rest if rest.strip() else "")


class ShortcutMiddleware(BaseMiddleware):
    def __init__(self, shortcuts: Shortcuts):
        self._shortcuts = shortcuts

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.text and event.from_user:
            expanded = await self._shortcuts.expand(event.from_user.id, event.text)
            if expanded is not None:
                event = event.model_copy(update={"text": expanded, "entities": None})
        return await handler(event, data)
