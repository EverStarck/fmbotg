"""Who in a group the bot can count: linked members it has seen there, minus blocked ones."""

from .db import Database, User
from .domain import UserFacingError

NOBODY_LINKED = "Nobody in this chat has connected Last.fm yet. Use /login, then send any message here."


class Members:
    def __init__(self, db: Database, max_members: int):
        self._db = db
        self.max_members = max_members

    async def linked(self, chat_id: int) -> list[User]:
        """Most recently active first, at most `max_members`."""
        return await self._db.chat_lastfm_users(chat_id, self.max_members)

    async def require_linked(self, chat_id: int) -> list[User]:
        members = await self.linked(chat_id)
        if not members:
            raise UserFacingError(NOBODY_LINKED)
        return members

    async def indexed(self, chat_id: int) -> list[User]:
        """Members whose whole history is imported, for commands that read the play index."""
        return [m for m in await self.linked(chat_id) if m.indexed_at is not None]

    async def require_indexed(self, chat_id: int, what: str) -> list[User]:
        members = await self.indexed(chat_id)
        if not members:
            raise UserFacingError(f"{what} uses imported histories, and nobody here has one yet. "
                                  "It's imported automatically after /login.")
        return members
