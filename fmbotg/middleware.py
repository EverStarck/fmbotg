"""Remembers which Telegram users are in which group, so /whoknows has a member list."""

import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, F, Router
from aiogram.types import ChatMemberUpdated, Message, TelegramObject

from .cache import TTLCache
from .db import Database

log = logging.getLogger(__name__)


class TrackMembersMiddleware(BaseMiddleware):
    def __init__(self, db: Database):
        self._db = db
        # Skip the database write when we saw this user in this chat recently.
        self._recent = TTLCache(ttl_seconds=600, max_items=50_000)

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and event.from_user and not event.from_user.is_bot:
            user = event.from_user
            chat_id = event.chat.id if event.chat.type in ("group", "supergroup") else None
            key = (chat_id, user.id, user.username)
            if self._recent.get(key) is None:
                try:
                    if await self._db.touch(user.id, user.username, user.full_name, chat_id):
                        self._recent.set(key, True)
                except Exception:
                    log.exception("Failed to record chat member %s in %s", user.id, chat_id)

            if event.left_chat_member and chat_id is not None:
                try:
                    await self._db.remove_member(chat_id, event.left_chat_member.id)
                except Exception:
                    log.exception("Failed to remove chat member %s from %s", event.left_chat_member.id, chat_id)
        return await handler(event, data)


membership_router = Router(name="membership")


@membership_router.chat_member()
async def on_chat_member(update: ChatMemberUpdated, db: Database) -> None:
    """Only delivered when the bot is a group admin; covers kicks and silent leaves."""
    if update.new_chat_member.status in ("left", "kicked"):
        await db.remove_member(update.chat.id, update.new_chat_member.user.id)


@membership_router.message(F.migrate_from_chat_id)
async def on_upgraded_to_supergroup(message: Message, db: Database) -> None:
    """Telegram gives a group a new id when it becomes a supergroup; keep its members, crowns and settings."""
    await db.migrate_chat(message.migrate_from_chat_id, message.chat.id)
    log.info("Moved chat %s to %s", message.migrate_from_chat_id, message.chat.id)
