"""Users: accounts, settings, moderation flags, friends and shortcuts."""

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError

from ..domain import utcnow
from .models import ChatMember, Friend, User, UserShortcut
from ._base import _Base, _only


class UserQueries(_Base):
    async def get_user(self, telegram_id: int) -> User | None:
        async with self.sessions() as session:
            return await session.scalar(select(User).where(User.telegram_id == telegram_id))

    async def get_user_by_id(self, user_id: int) -> User | None:
        async with self.sessions() as session:
            return await session.get(User, user_id)

    async def get_user_by_handle(self, handle: str) -> User | None:
        async with self.sessions() as session:
            # Handles move between people; the most recently seen holder is the current one
            return await session.scalar(
                select(User).where(func.lower(User.telegram_username) == handle.lstrip("@").lower())
                .order_by(User.last_used.desc()).limit(1)
            )

    async def get_user_by_lastfm(self, lastfm_username: str) -> User | None:
        async with self.sessions() as session:
            return await session.scalar(
                select(User).where(func.lower(User.lastfm_username) == lastfm_username.lower()).limit(1)
            )

    async def link_lastfm(self, telegram_id: int, lastfm_username: str, session_key: str) -> User:
        """Links an account. Switching to a different Last.fm account drops the old play index."""
        async with self.sessions() as session:
            user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
            if user is None:
                user = User(telegram_id=telegram_id)
                session.add(user)
                await session.flush()
            elif (user.lastfm_username or "").lower() != lastfm_username.lower():
                await self._drop_index(session, user)
            user.lastfm_username = lastfm_username
            user.session_key = session_key
            user.last_used = utcnow()
            await session.commit()
            return user

    async def unlink_lastfm(self, telegram_id: int) -> bool:
        async with self.sessions() as session:
            user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
            if user is None or user.lastfm_username is None:
                return False
            await self._drop_index(session, user)
            user.lastfm_username = None
            user.session_key = None
            await session.commit()
            return True

    async def update_settings(self, telegram_id: int, **values) -> None:
        allowed = {"timezone", "fm_mode", "fm_template", "privacy_level"}
        _only(values, allowed)
        async with self.sessions() as session:
            await session.execute(update(User).where(User.telegram_id == telegram_id).values(**values))
            await session.commit()

    async def touch(self, telegram_id: int, handle: str | None, display_name: str, chat_id: int | None) -> bool:
        """Record that a Telegram user was seen, optionally in a group chat. False if a race lost the write."""
        async with self.sessions() as session:
            user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
            if user is None:
                user = User(telegram_id=telegram_id)
                session.add(user)
            if handle and (user.telegram_username or "").lower() != handle.lower():
                # Someone else may have had this handle before; it's ours now
                await session.execute(update(User).where(
                    func.lower(User.telegram_username) == handle.lower(), User.telegram_id != telegram_id,
                ).values(telegram_username=None))
            user.telegram_username = handle
            user.display_name = display_name[:128]
            user.last_used = utcnow()

            if chat_id is not None:
                member = await session.get(ChatMember, (chat_id, telegram_id))
                if member is None:
                    session.add(ChatMember(chat_id=chat_id, telegram_id=telegram_id))
                else:
                    member.last_seen = utcnow()
            try:
                await session.commit()
                return True
            except IntegrityError:
                # Two updates for a new user raced; the next message will record it
                await session.rollback()
                return False

    async def friends(self, user_id: int) -> list[str]:
        async with self.sessions() as session:
            rows = await session.scalars(
                select(Friend.lastfm_username).where(Friend.user_id == user_id).order_by(Friend.created_at))
            return list(rows)

    async def add_friend(self, user_id: int, lastfm_username: str) -> bool:
        """False if they were already a friend."""
        async with self.sessions() as session:
            exists = await session.scalar(select(Friend.id).where(
                Friend.user_id == user_id, Friend.lastfm_key == lastfm_username.lower()))
            if exists is not None:
                return False
            session.add(Friend(user_id=user_id, lastfm_username=lastfm_username, lastfm_key=lastfm_username.lower()))
            await session.commit()
            return True

    async def remove_friend(self, user_id: int, lastfm_username: str) -> bool:
        async with self.sessions() as session:
            result = await session.execute(delete(Friend).where(
                Friend.user_id == user_id, Friend.lastfm_key == lastfm_username.lower()))
            await session.commit()
            return result.rowcount > 0

    async def users_by_lastfm(self, names: list[str]) -> dict[str, User]:
        """Registered users by lowercase Last.fm name."""
        if not names:
            return {}
        async with self.sessions() as session:
            rows = await session.scalars(
                select(User).where(func.lower(User.lastfm_username).in_([n.lower() for n in names])))
            return {u.lastfm_username.lower(): u for u in rows}

    async def shortcuts(self, telegram_id: int) -> dict[str, str]:
        async with self.sessions() as session:
            rows = await session.execute(
                select(UserShortcut.name, UserShortcut.output)
                .join(User, User.id == UserShortcut.user_id).where(User.telegram_id == telegram_id)
                .order_by(UserShortcut.name))
            return {name: output for name, output in rows}

    async def set_shortcut(self, user_id: int, name: str, output: str) -> bool:
        """Add or replace. Returns True if it was new."""
        async with self.sessions() as session:
            existing = await session.scalar(select(UserShortcut).where(
                UserShortcut.user_id == user_id, UserShortcut.name == name))
            if existing is None:
                session.add(UserShortcut(user_id=user_id, name=name, output=output))
            else:
                existing.output = output
            await session.commit()
            return existing is None

    async def remove_shortcut(self, user_id: int, name: str) -> bool:
        async with self.sessions() as session:
            result = await session.execute(delete(UserShortcut).where(
                UserShortcut.user_id == user_id, UserShortcut.name == name))
            await session.commit()
            return result.rowcount > 0

    async def set_user_flag(self, user_id: int, **values) -> None:
        _only(values, {"bot_banned", "global_hidden"})
        async with self.sessions() as session:
            await session.execute(update(User).where(User.id == user_id).values(**values))
            await session.commit()

    async def banned_telegram_ids(self) -> set[int]:
        async with self.sessions() as session:
            return set(await session.scalars(select(User.telegram_id).where(User.bot_banned.is_(True))))

    async def flagged_users(self) -> list[User]:
        async with self.sessions() as session:
            return list(await session.scalars(select(User).where(or_(User.bot_banned.is_(True),
                                                                        User.global_hidden.is_(True)))))
