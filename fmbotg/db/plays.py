"""The play index: importing scrobbles and every query over them."""

from datetime import timedelta

from sqlalchemy import func, insert, or_, select, update

from ..domain import utcnow
from ..names import name_key
from ..periods import ALL_TIME_END
from .models import SavedStreak, User, UserPlay
from .rows import AggregateRow, PlayRow
from ._base import _Base


class PlayQueries(_Base):
    async def begin_full_index(self, user_id: int) -> None:
        async with self.sessions() as session:
            user = await session.get(User, user_id)
            await self._drop_index(session, user, keep_streaks=True)
            user.updated_at = utcnow()
            await session.commit()

    async def reset_index(self, user_id: int) -> None:
        """Forget an import that turned out to be for an account the user no longer has."""
        async with self.sessions() as session:
            user = await session.get(User, user_id)
            if user is not None:
                await self._drop_index(session, user, keep_streaks=True)
                await session.commit()

    async def insert_plays(self, user_id: int, plays: list[PlayRow]) -> None:
        if not plays:
            return
        async with self.sessions() as session:
            await session.execute(insert(UserPlay), [p.as_insert(user_id) for p in plays])
            await session.commit()

    async def finish_index(self, user_id: int, last_scrobble_ts: int, full: bool) -> None:
        values = {"updated_at": utcnow(), "last_scrobble_ts": last_scrobble_ts}
        if full:
            values["indexed_at"] = utcnow()
        async with self.sessions() as session:
            await session.execute(update(User).where(User.id == user_id).values(**values))
            await session.commit()

    async def mark_attempt(self, user_id: int) -> None:
        async with self.sessions() as session:
            await session.execute(update(User).where(User.id == user_id).values(updated_at=utcnow()))
            await session.commit()

    async def users_due_for_update(self, limit: int, stale_after: timedelta,
                                   active_within: timedelta) -> list[User]:
        """Indexed users who used the bot recently and haven't been updated for a while."""
        now = utcnow()
        async with self.sessions() as session:
            rows = await session.scalars(
                select(User)
                .where(User.lastfm_username.is_not(None), User.indexed_at.is_not(None),
                       User.last_used > now - active_within, User.updated_at < now - stale_after)
                .order_by(User.updated_at)
                .limit(limit)
            )
            return list(rows)

    async def users_needing_import(self, limit: int, retry_after: timedelta) -> list[User]:
        """Linked users without a completed import, e.g. after a restart lost the queue."""
        async with self.sessions() as session:
            rows = await session.scalars(
                select(User)
                .where(User.lastfm_username.is_not(None), User.indexed_at.is_(None),
                       or_(User.updated_at.is_(None), User.updated_at < utcnow() - retry_after))
                .limit(limit)
            )
            return list(rows)

    async def play_count(self, user_id: int) -> int:
        async with self.sessions() as session:
            return await session.scalar(select(func.count()).where(UserPlay.user_id == user_id)) or 0

    async def playcounts(self, kind: str, subject: tuple[str, ...], user_ids: list[int]) -> dict[int, int]:
        """Per-user playcount for an artist, album or track among `user_ids` (who knows)."""
        if not user_ids:
            return {}
        conditions = [UserPlay.user_id.in_(user_ids), *self._subject_conditions(kind, subject)]
        async with self.sessions() as session:
            rows = await session.execute(
                select(UserPlay.user_id, func.count()).where(*conditions).group_by(UserPlay.user_id)
            )
            return {user_id: count for user_id, count in rows}

    async def top_from_plays(self, user_id: int, kind: str, from_ts: int, to_ts: int,
                             limit: int = 1000, artist: str | None = None) -> list[AggregateRow]:
        """Top artists/albums/tracks in [from_ts, to_ts). Names are matched case-insensitively."""
        key_columns, name_column = self._kind_columns(kind)
        conditions = [UserPlay.user_id == user_id, UserPlay.played_at >= from_ts, UserPlay.played_at < to_ts]
        if kind == "albums":
            conditions.append(UserPlay.album_key.is_not(None))
        if artist is not None:
            conditions.append(UserPlay.artist_key == name_key(artist))

        playcount = func.count().label("playcount")
        stmt = (
            select(func.max(name_column), func.max(UserPlay.artist), playcount)
            .where(*conditions)
            .group_by(*key_columns)
            .order_by(playcount.desc(), func.max(name_column))
            .limit(limit)
        )
        async with self.sessions() as session:
            rows = await session.execute(stmt)
            return [AggregateRow(name, artist if kind != "artists" else None, count)
                    for name, artist, count in rows]

    async def subject_stats(self, user_id: int, kind: str, subject: tuple[str, ...],
                            from_ts: int = 0, to_ts: int = ALL_TIME_END) -> tuple[int, int | None, int | None]:
        """(playcount, first play, last play) of an artist/album/track for one user."""
        stmt = select(func.count(), func.min(UserPlay.played_at), func.max(UserPlay.played_at)).where(
            UserPlay.user_id == user_id, UserPlay.played_at >= from_ts, UserPlay.played_at < to_ts,
            *self._subject_conditions(kind, subject),
        )
        async with self.sessions() as session:
            count, first, last = (await session.execute(stmt)).one()
            return count, first, last

    async def recent_plays(self, user_id: int, before_ts: int, limit: int) -> list[UserPlay]:
        """Newest first, strictly before `before_ts`."""
        async with self.sessions() as session:
            rows = await session.scalars(
                select(UserPlay)
                .where(UserPlay.user_id == user_id, UserPlay.played_at < before_ts)
                .order_by(UserPlay.played_at.desc(), UserPlay.id.desc())
                .limit(limit)
            )
            return list(rows)

    async def plays_between(self, user_id: int, from_ts: int, to_ts: int) -> list[UserPlay]:
        async with self.sessions() as session:
            rows = await session.scalars(
                select(UserPlay)
                .where(UserPlay.user_id == user_id, UserPlay.played_at >= from_ts, UserPlay.played_at < to_ts)
                .order_by(UserPlay.played_at)
            )
            return list(rows)

    async def count_plays(self, user_id: int, from_ts: int, to_ts: int) -> int:
        async with self.sessions() as session:
            return await session.scalar(
                select(func.count()).where(UserPlay.user_id == user_id, UserPlay.played_at >= from_ts,
                                           UserPlay.played_at < to_ts)
            ) or 0

    async def discoveries(self, user_id: int, from_ts: int, to_ts: int, limit: int = 1000) -> list[tuple[str, int, int]]:
        """Artists first played in [from_ts, to_ts): (artist, first play, all-time plays), most played first."""
        first = func.min(UserPlay.played_at)
        plays = func.count().label("plays")
        stmt = (
            select(func.max(UserPlay.artist), first, plays)
            .where(UserPlay.user_id == user_id)
            .group_by(UserPlay.artist_key)
            .having(first >= from_ts, first < to_ts)
            .order_by(plays.desc(), first)
            .limit(limit)
        )
        async with self.sessions() as session:
            return [(artist, ts, count) for artist, ts, count in await session.execute(stmt)]

    async def track_counts(self, user_id: int, artist: str, tracks: list[str]) -> dict[str, int]:
        """Plays per track name (match key) for one artist, regardless of album."""
        keys = [name_key(t) for t in tracks]
        async with self.sessions() as session:
            rows = await session.execute(
                select(UserPlay.track_key, func.count())
                .where(UserPlay.user_id == user_id, UserPlay.artist_key == name_key(artist),
                       UserPlay.track_key.in_(keys))
                .group_by(UserPlay.track_key)
            )
            return {key: count for key, count in rows}

    async def listening_gaps(self, user_id: int, kind: str, min_plays: int,
                             min_gap_seconds: int) -> list[tuple[str, str, int, int, int]]:
        """Things you came back to after a long break (port of PlayBuilders.ListeningGapsAsync).

        Returns (name, artist, resumed at, gap seconds, total plays) using the first long gap of each,
        longest gaps first.
        """
        keys, name_col = self._kind_columns(kind)
        previous = func.lag(UserPlay.played_at).over(partition_by=keys, order_by=UserPlay.played_at)
        total = func.count().over(partition_by=keys)
        conditions = [UserPlay.user_id == user_id]
        if kind == "albums":
            conditions.append(UserPlay.album_key.is_not(None))
        inner = (
            select(*[k.label(f"k{i}") for i, k in enumerate(keys)], name_col.label("name"),
                   UserPlay.artist.label("artist"), UserPlay.played_at.label("played_at"),
                   previous.label("previous"), total.label("total"))
            .where(*conditions)
            .subquery()
        )
        stmt = (
            select(inner)
            .where(inner.c.previous.is_not(None), inner.c.played_at - inner.c.previous >= min_gap_seconds,
                   inner.c.total >= min_plays)
            .order_by(inner.c.played_at)
        )
        seen: set[tuple] = set()
        gaps = []
        async with self.sessions() as session:
            for row in await session.execute(stmt):
                key = tuple(row[i] for i in range(len(keys)))
                if key in seen:
                    continue  # FMBot reports the first long gap per item
                seen.add(key)
                gaps.append((row.name, row.artist, row.played_at, row.played_at - row.previous, row.total))
        gaps.sort(key=lambda g: -g[3])
        return gaps

    async def search_library(self, user_id: int, kind: str, text: str, limit: int = 10) -> list[AggregateRow]:
        """Your artists/albums/tracks whose name contains `text`, most played first."""
        keys, name_col = self._kind_columns(kind)
        key_col = keys[-1]
        playcount = func.count().label("playcount")
        stmt = (
            select(func.max(name_col), func.max(UserPlay.artist), playcount)
            .where(UserPlay.user_id == user_id, key_col.contains(name_key(text), autoescape=True))
            .group_by(*keys)
            .order_by(playcount.desc())
            .limit(limit)
        )
        async with self.sessions() as session:
            return [AggregateRow(n, a if kind != "artists" else None, c) for n, a, c in await session.execute(stmt)]

    async def subject_timestamps(self, user_id: int, kind: str, subject: tuple[str, ...]) -> list[int]:
        async with self.sessions() as session:
            rows = await session.scalars(
                select(UserPlay.played_at).where(UserPlay.user_id == user_id, *self._subject_conditions(kind, subject))
            )
            return list(rows)

    async def save_streak(self, user_id: int, streak: "SavedStreak") -> bool:
        """Insert, or update the saved streak with the same start. Returns True if it was new."""
        async with self.sessions() as session:
            existing = await session.scalar(select(SavedStreak).where(
                SavedStreak.user_id == user_id, SavedStreak.started == streak.started))
            if existing is None:
                streak.user_id = user_id
                session.add(streak)
            else:
                for column in ("artist", "album", "track", "artist_count", "album_count", "track_count", "ended"):
                    setattr(existing, column, getattr(streak, column))
            await session.commit()
            return existing is None

    async def saved_streaks(self, user_id: int, limit: int = 15) -> list[SavedStreak]:
        async with self.sessions() as session:
            rows = await session.scalars(
                select(SavedStreak).where(SavedStreak.user_id == user_id).order_by(SavedStreak.started.desc()).limit(limit)
            )
            return list(rows)

    async def global_playcounts(self, kind: str, subject: tuple[str, ...], limit: int = 100) -> list[tuple[User, int]]:
        """Top listeners of an artist/album/track among everyone with an imported history."""
        plays = func.count().label("plays")
        stmt = (
            select(User, plays)
            .join(UserPlay, UserPlay.user_id == User.id)
            .where(User.indexed_at.is_not(None), User.lastfm_username.is_not(None), User.global_hidden.is_not(True),
                   *self._subject_conditions(kind, subject))
            .group_by(User.id)
            .order_by(plays.desc(), User.id)
            .limit(limit)
        )
        async with self.sessions() as session:
            return [(user, count) for user, count in await session.execute(stmt)]

    async def group_top(self, user_ids: list[int], kind: str, from_ts: int, to_ts: int,
                        limit: int = 100) -> list[tuple[str, str | None, int, int]]:
        """(name, artist, plays, listeners) across several people's play index (server top lists)."""
        if not user_ids:
            return []
        keys, name_col = self._kind_columns(kind)
        conditions = [UserPlay.user_id.in_(user_ids), UserPlay.played_at >= from_ts, UserPlay.played_at < to_ts]
        if kind == "albums":
            conditions.append(UserPlay.album_key.is_not(None))
        plays = func.count().label("plays")
        stmt = (
            select(func.max(name_col), func.max(UserPlay.artist), plays, func.count(UserPlay.user_id.distinct()))
            .where(*conditions)
            .group_by(*keys)
            .order_by(plays.desc(), func.max(name_col))
            .limit(limit)
        )
        async with self.sessions() as session:
            return [(name, artist if kind != "artists" else None, count, listeners)
                    for name, artist, count, listeners in await session.execute(stmt)]

    async def track_playcounts(self, user_ids: list[int], from_ts: int, to_ts: int
                               ) -> list[tuple[int, str, str, int]]:
        """(user id, artist, track, plays) per user and track in a range, for listening time."""
        if not user_ids:
            return []
        plays = func.count()
        stmt = (
            select(UserPlay.user_id, func.max(UserPlay.artist), func.max(UserPlay.track), plays)
            .where(UserPlay.user_id.in_(user_ids), UserPlay.played_at >= from_ts, UserPlay.played_at < to_ts)
            .group_by(UserPlay.user_id, UserPlay.artist_key, UserPlay.track_key)
        )
        async with self.sessions() as session:
            return [(u, a, t, c) for u, a, t, c in await session.execute(stmt)]
