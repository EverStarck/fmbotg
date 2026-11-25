"""Crowns per group and artist."""

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from ..domain import utcnow
from ..names import name_key
from .models import Crown, User, UserPlay
from ._base import _Base


class CrownQueries(_Base):
    async def active_crown(self, chat_id: int, artist: str) -> Crown | None:
        async with self.sessions() as session:
            return await session.scalar(select(Crown).where(
                Crown.chat_id == chat_id, Crown.artist_key == name_key(artist), Crown.active.is_(True)))

    async def crown_history(self, chat_id: int, artist: str, limit: int = 10) -> list[tuple[Crown, User]]:
        async with self.sessions() as session:
            rows = await session.execute(
                select(Crown, User).join(User, User.id == Crown.user_id)
                .where(Crown.chat_id == chat_id, Crown.artist_key == name_key(artist))
                .order_by(Crown.created_at.desc(), Crown.id.desc()).limit(limit))
            return [(crown, user) for crown, user in rows]

    async def set_crown(self, chat_id: int, artist: str, user_id: int, playcount: int,
                        seeded: bool = False) -> Crown:
        """Give the artist's crown in this chat to `user_id`, or update the holder's playcount."""
        for attempt in range(2):
            async with self.sessions() as session:
                current = await session.scalar(select(Crown).where(
                    Crown.chat_id == chat_id, Crown.artist_key == name_key(artist), Crown.active.is_(True)))
                if current is not None and current.user_id == user_id:
                    current.playcount = playcount
                    current.updated_at = utcnow()
                    crown = current
                else:
                    if current is not None:
                        current.active = False
                        current.updated_at = utcnow()
                    crown = Crown(chat_id=chat_id, user_id=user_id, artist=artist[:512], artist_key=name_key(artist),
                                  playcount=playcount, start_playcount=playcount, active=True, seeded=seeded)
                    session.add(crown)
                try:
                    await session.commit()
                    return crown
                except IntegrityError:
                    # Another /whoknows handed out this crown at the same moment: redo against theirs
                    await session.rollback()
                    if attempt:
                        raise
        raise AssertionError("unreachable")

    async def update_crown_playcount(self, crown_id: int, playcount: int) -> None:
        async with self.sessions() as session:
            await session.execute(update(Crown).where(Crown.id == crown_id).values(
                playcount=playcount, updated_at=utcnow()))
            await session.commit()

    async def delete_crowns(self, chat_id: int, artist: str | None = None) -> int:
        """Remove crowns (all history) for one artist, or for the whole chat."""
        conditions = [Crown.chat_id == chat_id]
        if artist is not None:
            conditions.append(Crown.artist_key == name_key(artist))
        async with self.sessions() as session:
            result = await session.execute(delete(Crown).where(*conditions))
            await session.commit()
            return result.rowcount

    async def user_crowns(self, chat_id: int, user_id: int) -> list[Crown]:
        async with self.sessions() as session:
            rows = await session.scalars(select(Crown).where(
                Crown.chat_id == chat_id, Crown.user_id == user_id, Crown.active.is_(True))
                .order_by(Crown.playcount.desc()))
            return list(rows)

    async def crown_leaderboard(self, chat_id: int, user_ids: list[int], limit: int = 15) -> list[tuple[User, int]]:
        crowns = func.count().label("crowns")
        async with self.sessions() as session:
            rows = await session.execute(
                select(User, crowns).join(Crown, Crown.user_id == User.id)
                .where(Crown.chat_id == chat_id, Crown.active.is_(True), User.id.in_(user_ids))
                .group_by(User.id).order_by(crowns.desc(), User.id).limit(limit))
            return [(user, count) for user, count in rows]

    async def crown_candidates(self, user_ids: list[int], threshold: int) -> list[tuple[str, int, int]]:
        """(artist, user id, plays) for every artist someone has at least `threshold` plays of."""
        if not user_ids:
            return []
        plays = func.count().label("plays")
        stmt = (
            select(func.max(UserPlay.artist), UserPlay.user_id, plays)
            .where(UserPlay.user_id.in_(user_ids))
            .group_by(UserPlay.artist_key, UserPlay.user_id)
            .having(plays >= threshold)
        )
        async with self.sessions() as session:
            return [(artist, user_id, count) for artist, user_id, count in await session.execute(stmt)]

    async def seed_crowns(self, chat_id: int, picks: list[tuple[str, int, int]]) -> int:
        """Create crowns from (artist, user id, plays) for artists that have no active crown yet."""
        async with self.sessions() as session:
            taken = set(await session.scalars(select(Crown.artist_key).where(
                Crown.chat_id == chat_id, Crown.active.is_(True))))
            now = utcnow()
            rows = [{"chat_id": chat_id, "user_id": user_id, "artist": artist[:512], "artist_key": name_key(artist),
                     "playcount": plays, "start_playcount": plays, "active": True, "seeded": True,
                     "created_at": now, "updated_at": now}
                    for artist, user_id, plays in picks if name_key(artist) not in taken]
            if rows:
                await session.execute(insert(Crown), rows)
            await session.commit()
            return len(rows)
