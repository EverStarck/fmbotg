from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, String, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    telegram_username: Mapped[str | None] = mapped_column(String(64), index=True)
    display_name: Mapped[str | None] = mapped_column(String(128))
    lastfm_username: Mapped[str | None] = mapped_column(String(64))
    session_key: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ChatMember(Base):
    """Telegram has no API to list group members, so we remember who we've seen in each chat."""

    __tablename__ = "chat_members"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Database:
    def __init__(self, url: str):
        self.engine: AsyncEngine = create_async_engine(url)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    async def create_schema(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        await self.engine.dispose()

    # --- users --------------------------------------------------------------------------

    async def get_user(self, telegram_id: int) -> User | None:
        async with self.sessions() as session:
            return await session.scalar(select(User).where(User.telegram_id == telegram_id))

    async def get_user_by_handle(self, handle: str) -> User | None:
        async with self.sessions() as session:
            return await session.scalar(
                select(User).where(func.lower(User.telegram_username) == handle.lstrip("@").lower())
            )

    async def link_lastfm(self, telegram_id: int, lastfm_username: str, session_key: str) -> None:
        async with self.sessions() as session:
            user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
            if user is None:
                user = User(telegram_id=telegram_id)
                session.add(user)
            user.lastfm_username = lastfm_username
            user.session_key = session_key
            user.last_used = utcnow()
            await session.commit()

    async def unlink_lastfm(self, telegram_id: int) -> bool:
        async with self.sessions() as session:
            user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
            if user is None or user.lastfm_username is None:
                return False
            user.lastfm_username = None
            user.session_key = None
            await session.commit()
            return True

    async def touch(self, telegram_id: int, handle: str | None, display_name: str, chat_id: int | None) -> None:
        """Record that a Telegram user was seen, optionally in a group chat."""
        async with self.sessions() as session:
            user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
            if user is None:
                user = User(telegram_id=telegram_id)
                session.add(user)
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
            except IntegrityError:
                # Two updates for a new user raced; the other one won and that's fine.
                await session.rollback()

    async def remove_member(self, chat_id: int, telegram_id: int) -> None:
        async with self.sessions() as session:
            member = await session.get(ChatMember, (chat_id, telegram_id))
            if member is not None:
                await session.delete(member)
                await session.commit()

    async def chat_lastfm_users(self, chat_id: int, limit: int) -> list[User]:
        async with self.sessions() as session:
            rows = await session.scalars(
                select(User)
                .join(ChatMember, ChatMember.telegram_id == User.telegram_id)
                .where(ChatMember.chat_id == chat_id, User.lastfm_username.is_not(None))
                .order_by(ChatMember.last_seen.desc())
                .limit(limit)
            )
            return list(rows)
