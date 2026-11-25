"""Answers top lists and who knows from the play index when possible, Last.fm otherwise."""

import asyncio
from collections import Counter
from urllib.parse import quote_plus

from .cache import TTLCache
from .domain import Target, UserFacingError
from .db import AggregateRow, Database, User
from .names import name_key
from .formatting import esc
from .indexer import PAGE_SIZE, Indexer
from .lastfm import LastFmClient, LastFmError, TopItem, TopKind, TopList, pick_image
from .periods import ALL_TIME_END, Period, year_period
from .playlogic import Play, Streak

# Without an index, ranges are fetched live from Last.fm; cap it so one command can't
# page through someone's entire history.
MAX_LIVE_PAGES = 5
MAX_LIVE_RANGE_SECONDS = 8 * 86400


def music_url(artist: str, title: str | None = None, kind: str = "artists") -> str:
    base = f"https://www.last.fm/music/{quote_plus(artist)}"
    if kind == "albums" and title:
        return f"{base}/{quote_plus(title)}"
    if kind == "tracks" and title:
        return f"{base}/_/{quote_plus(title)}"
    return base


def aggregate(plays: list[tuple[str, str | None, str]], kind: TopKind) -> list[AggregateRow]:
    """(artist, album, track) tuples -> sorted rows, grouping names case-insensitively."""
    counts: Counter[tuple] = Counter()
    display: dict[tuple, tuple[str, str | None]] = {}
    for artist, album, track in plays:
        if kind == "artists":
            key, shown = (name_key(artist),), (artist, None)
        elif kind == "albums":
            if not album:
                continue
            key, shown = (name_key(artist), name_key(album)), (album, artist)
        else:
            key, shown = (name_key(artist), name_key(track)), (track, artist)
        counts[key] += 1
        display.setdefault(key, shown)
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], display[kv[0]][0].lower()))
    return [AggregateRow(display[key][0], display[key][1], count) for key, count in ordered]


class Stats:
    def __init__(self, db: Database, lastfm: LastFmClient, indexer: Indexer):
        self._db = db
        self._lastfm = lastfm
        self._indexer = indexer
        self._ranges = TTLCache(ttl_seconds=120, max_items=2_000)
        self._images = TTLCache(ttl_seconds=3600, max_items=20_000)
        self._all_time_artists = TTLCache(ttl_seconds=1800, max_items=5_000)

    # --- top lists ----------------------------------------------------------------------

    async def top(self, target: Target, kind: TopKind, period: Period, page: int, page_size: int,
                  billboard: bool = False) -> TopList:
        """Top list for a page. `billboard` compares ranks with the previous period of the same length
        (port of FMBot's billboard mode); both periods then come from the same range data."""
        if not period.is_range and not billboard:
            return await self._lastfm.top(target.lastfm_username, kind, period.api, limit=page_size, page=page)
        if billboard and period.days is None and period.year is None and period.calendar is None:
            raise UserFacingError("Billboard mode needs a period to compare with, e.g. weekly or monthly.")

        from_ts, to_ts = period.bounds(tz=target.tz)
        rows = await self._range_rows(target, kind, period, from_ts, to_ts)
        previous: dict[tuple, int] = {}
        if billboard:
            length = to_ts - from_ts
            if period.year is not None:  # compare with the whole previous year, not the same number of days
                from_prev, _ = year_period(period.year - 1).bounds(tz=target.tz)
                to_prev = from_ts
            else:
                from_prev, to_prev = from_ts - length, from_ts
            before = await self._range_rows(target, kind, period, from_prev, to_prev, cache_suffix="prev")
            previous = {self._row_key(r): rank for rank, r in enumerate(before, 1)}

        total_pages = max(1, -(-len(rows) // page_size))
        start = (page - 1) * page_size
        items = [
            TopItem(r.name, r.artist, r.playcount, music_url(r.artist or r.name, r.name, kind), None,
                    previous.get(self._row_key(r), 0) if billboard else None)
            for r in rows[start: start + page_size]
        ]
        return TopList(items=items, total=len(rows), page=page, total_pages=total_pages)

    @staticmethod
    def _row_key(row: AggregateRow) -> tuple:
        return name_key(row.name), name_key(row.artist)

    async def _range_rows(self, target: Target, kind: TopKind, period: Period, from_ts: int, to_ts: int,
                          cache_suffix: str = "") -> list[AggregateRow]:
        cache_key = (target.lastfm_username.lower(), kind, period.key, str(target.tz), cache_suffix)
        cached = self._ranges.get(cache_key)
        if cached is not None:
            return cached

        if target.user_id is not None and target.indexed:
            await self._indexer.update(target.user_id)
            rows = await self._db.top_from_plays(target.user_id, kind, from_ts, to_ts)
        elif to_ts - from_ts > MAX_LIVE_RANGE_SECONDS:
            if target.user_id is not None and self._indexer.is_importing(target.user_id):
                raise UserFacingError("Your scrobble history is still being imported. Try again in a bit.")
            raise UserFacingError(
                f"The {esc(period.label)} period needs an imported scrobble history. "
                "It's imported automatically after /login; /update starts it manually."
            )
        else:
            plays = await self._live_plays(target, from_ts, to_ts)
            rows = aggregate([(p.artist, p.album, p.track) for p in plays], kind)

        self._ranges.set(cache_key, rows)
        return rows

    async def _live_plays(self, target: Target, from_ts: int, to_ts: int) -> list[Play]:
        plays = []
        for page in range(1, MAX_LIVE_PAGES + 1):
            result = await self._lastfm.recent_tracks(
                target.lastfm_username, limit=PAGE_SIZE, session_key=target.session_key,
                page=page, from_ts=from_ts, to_ts=to_ts, extended=False,
            )
            plays += [Play(s.timestamp, s.artist, s.album or None, s.track) for s in result.tracks if s.timestamp]
            if page >= result.total_pages:
                return plays
        raise UserFacingError("That's too many scrobbles to count live. Connect with /login to import your history.")

    async def require_index(self, target: Target) -> None:
        """Raise a helpful error unless the target's history is imported; otherwise bring it up to date."""
        if target.user_id is None:
            raise UserFacingError("This needs an imported scrobble history, which only works for people "
                                  "who connected Last.fm to this bot.")
        if self._indexer.is_importing(target.user_id):
            raise UserFacingError("The scrobble history is still being imported. Try again in a bit.")
        if not target.indexed:
            raise UserFacingError("This needs an imported scrobble history. Run /update to import it.")
        await self._indexer.update(target.user_id)

    async def plays_window(self, target: Target, from_ts: int, to_ts: int) -> list[Play]:
        """Plays in [from_ts, to_ts), oldest first: from the index, or live for short windows."""
        if target.indexed and target.user_id is not None:
            await self._indexer.update(target.user_id)
            rows = await self._db.plays_between(target.user_id, from_ts, to_ts)
            return [Play(r.played_at, r.artist, r.album, r.track) for r in rows]
        if to_ts - from_ts > MAX_LIVE_RANGE_SECONDS:
            await self.require_index(target)
        return sorted(await self._live_plays(target, from_ts, to_ts), key=lambda p: p.played_at)

    async def streak(self, target: Target) -> Streak | None:
        """Current artist/album/track streak (port of PlayService.GetCurrentStreak)."""
        recent = await self._lastfm.recent_tracks(target.lastfm_username, limit=PAGE_SIZE,
                                                  session_key=target.session_key, extended=False)
        if not recent.tracks:
            return None
        plays = [Play(None if s.now_playing else s.timestamp, s.artist, s.album or None, s.track)
                 for s in recent.tracks]
        current, previous = plays[0], plays[1:]
        streak = Streak(current)
        if not streak.feed(previous) or not previous or not (target.indexed and target.user_id):
            return streak

        # Still going after a full page: continue from the play index.
        await self._indexer.update(target.user_id)
        before = previous[-1].played_at
        while streak.alive and before:
            rows = await self._db.recent_plays(target.user_id, before, PAGE_SIZE)
            if not rows:
                break
            streak.feed(Play(r.played_at, r.artist, r.album, r.track) for r in rows)
            before = rows[-1].played_at
        return streak

    async def album_image(self, artist: str, album: str) -> str | None:
        """Cover lookup for index-based charts, which have no images of their own."""
        key = (name_key(artist), name_key(album))
        cached = self._images.get(key, default=False)
        if cached is not False:
            return cached
        try:
            image = pick_image((await self._lastfm.album_info(artist, album)).get("image"))
        except LastFmError:
            image = None
        self._images.set(key, image)
        return image

    # --- who knows ----------------------------------------------------------------------

    async def canonical_subject(self, kind: str, subject: tuple[str, ...]) -> tuple[str, ...]:
        """Apply Last.fm's autocorrect ("beatles" -> "The Beatles") so index lookups match."""
        try:
            if kind == "artist":
                return (await self._lastfm.artist_info(subject[0]))["name"],
            if kind == "album":
                info = await self._lastfm.album_info(subject[0], subject[1])
                return info.get("artist", subject[0]), info.get("name", subject[1])
            info = await self._lastfm.track_info(subject[0], subject[1])
            return (info.get("artist") or {}).get("name", subject[0]), info.get("name", subject[1])
        except LastFmError:
            return subject

    async def whoknows_counts(self, kind: str, subject: tuple[str, ...], members: list[User],
                              caller_telegram_id: int) -> list[tuple[User, int | None]]:
        """Playcount per member: SQL for indexed users, live Last.fm for the rest (None on failure)."""
        caller = next((m for m in members if m.telegram_id == caller_telegram_id and m.indexed_at), None)
        if caller is not None:
            await self._indexer.update(caller.id)

        indexed = [m for m in members if m.indexed_at is not None and not self._indexer.is_importing(m.id)]
        sql_counts = await self._db.playcounts(kind, subject, [m.id for m in indexed])
        indexed_ids = {m.id for m in indexed}

        async def live(member: User) -> tuple[User, int | None]:
            try:
                return member, await self._lastfm.user_playcount(kind, subject, member.lastfm_username)
            except LastFmError:
                return member, None

        live_results = await asyncio.gather(*(live(m) for m in members if m.id not in indexed_ids))
        return [(m, sql_counts.get(m.id, 0)) for m in indexed] + list(live_results)

    async def global_whoknows(self, kind: str, subject: tuple[str, ...], caller: User | None,
                              limit: int = 100) -> list[tuple[User, int]]:
        """Top listeners among everyone with an imported history (port of the gwk commands)."""
        if caller is not None and caller.indexed_at is not None:
            await self._indexer.update(caller.id)
        rows = await self._db.global_playcounts(kind, subject, limit)
        return [(user, plays) for user, plays in rows if not self._indexer.is_importing(user.id)]

    async def all_time_artists(self, user: User, limit: int) -> list[tuple[str, int]] | None:
        """Someone's all-time top artists as (name, plays), cached for 30 minutes; None if Last.fm fails.

        From the play index once a full import is done (never from a half-finished one), else Last.fm.
        Works for unsaved Users too (friends who never used the bot).
        """
        key = (user.lastfm_username.lower(), limit)
        cached = self._all_time_artists.get(key)
        if cached is not None:
            return cached
        if user.id is not None and user.indexed_at is not None and not self._indexer.is_importing(user.id):
            rows = [(r.name, r.playcount) for r in await self._db.top_from_plays(user.id, "artists", 0, ALL_TIME_END,
                                                                                limit=limit)]
        else:
            try:
                top = await self._lastfm.top(user.lastfm_username, "artists", "overall", limit=limit)
            except LastFmError:
                return None
            rows = [(item.name, item.playcount) for item in top.items]
        self._all_time_artists.set(key, rows)
        return rows
