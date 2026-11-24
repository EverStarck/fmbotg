"""Cached artist, album and track data, censored music and bot totals."""

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from ..domain import utcnow
from ..names import name_key
from .models import AlbumMeta, ArtistMeta, CensoredMusic, ChatMember, Crown, TrackMeta, User, UserPlay
from ._base import _Base, _only


class MetaQueries(_Base):
    async def artist_meta(self, keys: list[str]) -> dict[str, ArtistMeta]:
        found: dict[str, ArtistMeta] = {}
        async with self.sessions() as session:
            for start in range(0, len(keys), 500):
                rows = await session.scalars(select(ArtistMeta).where(ArtistMeta.artist_key.in_(keys[start:start + 500])))
                found.update({row.artist_key: row for row in rows})
        return found

    async def save_artist_meta(self, artist: str, **values) -> None:
        """Upsert tags/tags_at or country_code/country/country_at for one artist."""
        _only(values, {"tags", "tags_at", "country_code", "country", "country_at", "image_url", "image_at",
                               "listeners", "listeners_at"})
        for attempt in range(2):
            async with self.sessions() as session:
                row = await session.get(ArtistMeta, name_key(artist))
                if row is None:
                    row = ArtistMeta(artist_key=name_key(artist), artist=artist[:512])
                    session.add(row)
                for key, value in values.items():
                    setattr(row, key, value)
                try:
                    await session.commit()
                    return
                except IntegrityError:
                    # The tag and country lookups for a new artist raced; update the other one's row
                    await session.rollback()
                    if attempt:
                        raise

    async def track_meta(self, keys: list[tuple[str, str]]) -> dict[tuple[str, str], TrackMeta]:
        found: dict[tuple[str, str], TrackMeta] = {}
        async with self.sessions() as session:
            for start in range(0, len(keys), 400):
                chunk = keys[start:start + 400]
                artists = {a for a, _ in chunk}
                rows = await session.scalars(select(TrackMeta).where(
                    TrackMeta.artist_key.in_(artists), TrackMeta.track_key.in_({t for _, t in chunk})))
                wanted = set(chunk)
                found.update({(r.artist_key, r.track_key): r for r in rows if (r.artist_key, r.track_key) in wanted})
        return found

    async def save_track_meta(self, artist: str, track: str, duration_ms: int, deezer_url: str | None) -> None:
        key = (name_key(artist), name_key(track))
        async with self.sessions() as session:
            row = await session.get(TrackMeta, key)
            if row is None:
                session.add(TrackMeta(artist_key=key[0], track_key=key[1], duration_ms=duration_ms,
                                      deezer_url=deezer_url, looked_up_at=utcnow()))
            else:
                row.duration_ms, row.deezer_url, row.looked_up_at = duration_ms, deezer_url, utcnow()
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()  # looked up twice at once; the other one saved it

    async def censored(self) -> list[CensoredMusic]:
        async with self.sessions() as session:
            return list(await session.scalars(select(CensoredMusic).order_by(CensoredMusic.artist)))

    async def add_censored(self, artist: str, album: str | None) -> bool:
        async with self.sessions() as session:
            session.add(CensoredMusic(artist=artist[:512], album=album and album[:512], artist_key=name_key(artist),
                                      album_key=name_key(album)))
            try:
                await session.commit()
                return True
            except IntegrityError:
                await session.rollback()
                return False

    async def remove_censored(self, artist: str, album: str | None) -> bool:
        async with self.sessions() as session:
            result = await session.execute(delete(CensoredMusic).where(
                CensoredMusic.artist_key == name_key(artist), CensoredMusic.album_key == name_key(album)))
            await session.commit()
            return result.rowcount > 0

    async def album_meta(self, artist: str, album: str) -> AlbumMeta | None:
        async with self.sessions() as session:
            return await session.get(AlbumMeta, (name_key(artist), name_key(album)))

    async def save_album_meta(self, artist: str, album: str, release_date: str) -> None:
        key = (name_key(artist), name_key(album))
        async with self.sessions() as session:
            row = await session.get(AlbumMeta, key)
            if row is None:
                session.add(AlbumMeta(artist_key=key[0], album_key=key[1], release_date=release_date,
                                      looked_up_at=utcnow()))
            else:
                row.release_date, row.looked_up_at = release_date, utcnow()
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()

    async def bot_stats(self) -> dict[str, int]:
        async with self.sessions() as session:
            async def count(stmt) -> int:
                return await session.scalar(stmt) or 0
            return {
                "users": await count(select(func.count()).select_from(User)),
                "linked": await count(select(func.count()).where(User.lastfm_username.is_not(None))),
                "imported": await count(select(func.count()).where(User.indexed_at.is_not(None))),
                "plays": await count(select(func.count()).select_from(UserPlay)),
                "groups": await count(select(func.count(ChatMember.chat_id.distinct()))),
                "crowns": await count(select(func.count()).where(Crown.active.is_(True))),
                "artists cached": await count(select(func.count()).select_from(ArtistMeta)),
                "tracks cached": await count(select(func.count()).select_from(TrackMeta)),
            }
