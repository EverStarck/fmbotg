"""Group chats: members, settings, blocked users and autoposts."""

from datetime import datetime

from sqlalchemy import delete, select, update

from .models import Autopost, ChatBlockedUser, ChatMember, ChatSettings, Crown, User
from ._base import _Base, _only


class ChatQueries(_Base):
    async def remove_member(self, chat_id: int, telegram_id: int) -> None:
        async with self.sessions() as session:
            member = await session.get(ChatMember, (chat_id, telegram_id))
            if member is not None:
                await session.delete(member)
                await session.commit()

    async def chat_lastfm_users(self, chat_id: int, limit: int) -> list[User]:
        """Linked members seen in this chat, most recently active first, without blocked ones."""
        blocked = select(ChatBlockedUser.user_id).where(ChatBlockedUser.chat_id == chat_id)
        async with self.sessions() as session:
            rows = await session.scalars(
                select(User)
                .join(ChatMember, ChatMember.telegram_id == User.telegram_id)
                .where(ChatMember.chat_id == chat_id, User.lastfm_username.is_not(None), User.id.not_in(blocked))
                .order_by(ChatMember.last_seen.desc())
                .limit(limit)
            )
            return list(rows)

    async def chat_settings(self, chat_id: int) -> ChatSettings:
        async with self.sessions() as session:
            return await session.get(ChatSettings, chat_id) or ChatSettings(chat_id=chat_id, crowns_disabled=False)

    async def update_chat_settings(self, chat_id: int, **values) -> None:
        _only(values, {"crown_threshold", "crowns_disabled", "disabled_commands"})
        async with self.sessions() as session:
            settings = await session.get(ChatSettings, chat_id)
            if settings is None:
                settings = ChatSettings(chat_id=chat_id, crowns_disabled=False)
                session.add(settings)
            for key, value in values.items():
                setattr(settings, key, value)
            await session.commit()

    async def block_user(self, chat_id: int, user_id: int) -> bool:
        async with self.sessions() as session:
            if await session.get(ChatBlockedUser, (chat_id, user_id)) is not None:
                return False
            session.add(ChatBlockedUser(chat_id=chat_id, user_id=user_id))
            await session.commit()
            return True

    async def unblock_user(self, chat_id: int, user_id: int) -> bool:
        async with self.sessions() as session:
            result = await session.execute(delete(ChatBlockedUser).where(
                ChatBlockedUser.chat_id == chat_id, ChatBlockedUser.user_id == user_id))
            await session.commit()
            return result.rowcount > 0

    async def blocked_users(self, chat_id: int) -> list[User]:
        async with self.sessions() as session:
            rows = await session.scalars(select(User).join(ChatBlockedUser, ChatBlockedUser.user_id == User.id)
                                         .where(ChatBlockedUser.chat_id == chat_id).order_by(ChatBlockedUser.created_at))
            return list(rows)

    async def autoposts(self, chat_id: int) -> list[Autopost]:
        async with self.sessions() as session:
            return list(await session.scalars(select(Autopost).where(Autopost.chat_id == chat_id).order_by(Autopost.id)))

    async def add_autopost(self, chat_id: int, content: str, schedule: str, all_time: bool,
                           next_post_at: datetime) -> Autopost:
        async with self.sessions() as session:
            post = Autopost(chat_id=chat_id, content=content, schedule=schedule, all_time=all_time, enabled=True,
                            next_post_at=next_post_at)
            session.add(post)
            await session.commit()
            return post

    async def update_autopost(self, autopost_id: int, **values) -> None:
        _only(values, {"enabled", "next_post_at", "last_posted_at"})
        async with self.sessions() as session:
            await session.execute(update(Autopost).where(Autopost.id == autopost_id).values(**values))
            await session.commit()

    async def remove_autopost(self, chat_id: int, autopost_id: int) -> bool:
        async with self.sessions() as session:
            result = await session.execute(delete(Autopost).where(
                Autopost.chat_id == chat_id, Autopost.id == autopost_id))
            await session.commit()
            return result.rowcount > 0

    async def due_autoposts(self, now: datetime, limit: int = 20) -> list[Autopost]:
        async with self.sessions() as session:
            return list(await session.scalars(
                select(Autopost).where(Autopost.enabled.is_(True), Autopost.next_post_at <= now)
                .order_by(Autopost.next_post_at).limit(limit)))

    async def migrate_chat(self, old_chat_id: int, new_chat_id: int) -> None:
        """A group became a supergroup and got a new id: move everything keyed by the chat."""
        async with self.sessions() as session:
            # Tables keyed by (chat, person): keep a row the new chat already has, else move the old one
            for model, other in ((ChatMember, ChatMember.telegram_id), (ChatBlockedUser, ChatBlockedUser.user_id)):
                taken = set(await session.scalars(select(other).where(model.chat_id == new_chat_id)))
                if taken:
                    await session.execute(delete(model).where(model.chat_id == old_chat_id, other.in_(taken)))
                await session.execute(update(model).where(model.chat_id == old_chat_id).values(chat_id=new_chat_id))
            if await session.get(ChatSettings, new_chat_id) is None:
                await session.execute(update(ChatSettings).where(ChatSettings.chat_id == old_chat_id)
                                      .values(chat_id=new_chat_id))
            for model in (Crown, Autopost):
                await session.execute(update(model).where(model.chat_id == old_chat_id).values(chat_id=new_chat_id))
            await session.commit()
