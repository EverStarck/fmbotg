"""Artist genres and countries (port of GenreService/TagService and CountryService).

Genres are an artist's Last.fm tags without the noise ("seen live", "british", "80s").
Countries come from MusicBrainz, which allows one request per second, so lookups run in a
background queue: a command waits a few seconds for what it needs, answers with what's known,
and the rest is saved for next time.
"""

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta

import httpx

from . import deezer
from .db import Database, name_key, utcnow
from .lastfm import LastFmClient, LastFmError

log = logging.getLogger(__name__)

MUSICBRAINZ_URL = "https://musicbrainz.org/ws/2/artist"
MUSICBRAINZ_RELEASE_GROUPS = "https://musicbrainz.org/ws/2/release-group"
MUSICBRAINZ_SPACING = 1.1  # MusicBrainz allows one request per second per client
TAGS_STALE_AFTER = timedelta(days=90)
COUNTRY_STALE_AFTER = timedelta(days=180)
IMAGES_STALE_AFTER = timedelta(days=60)
LISTENERS_STALE_AFTER = timedelta(days=30)
MAX_GENRES = 6
MIN_TAG_WEIGHT = 10  # Last.fm weights tags 0-100 relative to the artist's top tag

NOT_GENRES = {
    "seen live", "favorites", "favourites", "favorite", "favourite", "my favorite", "my favourites", "love",
    "beautiful", "awesome", "amazing", "cool", "good", "great", "best", "genius", "legend", "legends", "sexy",
    "female vocalists", "female vocalist", "male vocalists", "male vocalist", "vocalists", "female", "male",
    "albums i own", "spotify", "under 2000 listeners", "all", "music", "fip", "mellow", "chill", "sad", "happy",
    "american", "usa", "us", "british", "uk", "english", "england", "scottish", "irish", "welsh", "canadian",
    "australian", "new zealand", "german", "deutsch", "french", "swedish", "norwegian", "danish", "finnish",
    "icelandic", "dutch", "belgian", "spanish", "italian", "portuguese", "russian", "polish", "ukrainian",
    "japanese", "japan", "korean", "korea", "chinese", "taiwanese", "brazilian", "brasil", "mexican", "mexico",
    "argentinian", "argentina", "chilean", "colombian", "puerto rico", "puerto rican", "venezuelan", "peruvian",
    "cuban", "jamaican", "african", "nigerian", "south african", "indian", "turkish", "greek", "israeli",
}
_DECADE = re.compile(r"^(\d{2}|\d{4})'?s$")

# Typed country names that don't match MusicBrainz's spelling
COUNTRY_ALIASES = {"uk": "GB", "england": "GB", "britain": "GB", "usa": "US", "america": "US",
                   "korea": "KR", "south korea": "KR", "russia": "RU", "holland": "NL"}


def genre_key(name: str) -> str:
    """'Hip Hop', 'hip-hop' and 'hiphop' are the same genre."""
    return re.sub(r"[\s_\-]+", "", name.lower())


def clean_tags(tags: list[tuple[str, int]]) -> list[str]:
    """Last.fm tags -> genres, strongest first, lowercase, without duplicates or non-genres."""
    genres, seen = [], set()
    for name, weight in tags:
        name = " ".join(name.lower().split())
        if weight < MIN_TAG_WEIGHT or name in NOT_GENRES or _DECADE.match(name) or "seen" in name.split():
            continue
        if genre_key(name) not in seen and len(name) <= 40:
            seen.add(genre_key(name))
            genres.append(name)
        if len(genres) == MAX_GENRES:
            break
    return genres


def flag(code: str | None) -> str:
    """'GB' -> 🇬🇧. MusicBrainz's XW (worldwide) and XE (Europe) have no flag."""
    if not code or len(code) != 2 or not code.isalpha() or code.upper().startswith("X"):
        return ""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in code.upper())


def _mb_quote(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def pick_release_date(artist: str, album: str, groups: list[dict]) -> str | None:
    """The earliest first-release date among exact title and artist matches (editions included)."""
    from .games import normalize_answer, remove_edition_suffix

    title = normalize_answer(remove_edition_suffix(album))
    dates = []
    for group in groups:
        credits = {normalize_answer(c.get("name") or (c.get("artist") or {}).get("name", ""))
                   for c in group.get("artist-credit") or []}
        if (int(group.get("score") or 0) >= 90 and normalize_answer(group.get("title", "")) == title
                and normalize_answer(artist) in credits and group.get("first-release-date")):
            dates.append(group["first-release-date"])
    return min(dates) if dates else None


def pick_musicbrainz_artist(artist: str, results: list[dict]) -> tuple[str, str] | None:
    """(country code, country name) for the best exact-name match with a country."""
    wanted = artist.casefold()
    for found in results:
        names = {found.get("name", "").casefold(), found.get("sort-name", "").casefold()}
        names |= {a.get("name", "").casefold() for a in found.get("aliases") or []}
        if wanted not in names or int(found.get("score") or 0) < 90:
            continue
        code = found.get("country")
        if not code:
            continue  # same name but no country; a less popular namesake might have one, but don't guess
        area = found.get("area") or {}
        name = area.get("name") if area.get("type") == "Country" else None
        return code, name or code
    return None


FAILED = object()  # a lookup that should be retried later, as opposed to "no genres/country known"


@dataclass(frozen=True)
class Lookup:
    """Result of asking for metadata of many artists at once."""

    values: dict[str, object]  # artist key -> list of genres, or (code, name) / None for countries
    pending: int  # still being looked up; try again later for a complete answer


class _Queue:
    """Fetches each artist once, in the order asked, with limited concurrency and optional spacing."""

    def __init__(self, fetch: Callable[..., Awaitable[object]], workers: int, spacing: float = 0.0,
                 key: Callable[..., object] = name_key):
        self._fetch = fetch
        self._workers_wanted = workers
        self._spacing = spacing
        self._key = key
        self._queue: asyncio.Queue[tuple[tuple, asyncio.Future]] = asyncio.Queue()
        self._inflight: dict[object, asyncio.Future] = {}
        self._workers: list[asyncio.Task] = []

    def request(self, *item: str) -> asyncio.Future:
        key = self._key(*item)
        future = self._inflight.get(key)
        if future is None:
            future = asyncio.get_running_loop().create_future()
            self._inflight[key] = future
            self._queue.put_nowait((item, future))
        if not self._workers:
            self._workers = [asyncio.create_task(self._work()) for _ in range(self._workers_wanted)]
        return future

    async def _work(self) -> None:
        while True:
            item, future = await self._queue.get()
            try:
                future.set_result(await self._fetch(*item))
            except Exception as exc:  # one bad artist mustn't stop the queue
                log.warning("Metadata lookup for %r failed: %s", item, exc)
                future.set_result(FAILED)
            finally:
                self._inflight.pop(self._key(*item), None)
            if self._spacing:
                await asyncio.sleep(self._spacing)

    async def close(self) -> None:
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers = []


class Metadata:
    def __init__(self, db: Database, lastfm: LastFmClient, http: httpx.AsyncClient, user_agent: str):
        self._db = db
        self._lastfm = lastfm
        self._http = http
        self._user_agent = user_agent
        self._tags = _Queue(self._fetch_tags, workers=6)
        self._mb_lock = asyncio.Lock()  # one MusicBrainz request at a time, shared by countries and release dates
        self._mb_last = 0.0
        self._countries = _Queue(self._fetch_country, workers=1)
        self._releases = _Queue(self._fetch_release, workers=1,
                                key=lambda artist, album: (name_key(artist), name_key(album)))
        self._images = _Queue(self._fetch_image, workers=3, spacing=0.2)  # Deezer: 50 requests per 5 seconds
        self._listeners = _Queue(self._fetch_listeners, workers=6)
        self._tracks = _Queue(self._fetch_track, workers=4, spacing=0.1,
                              key=lambda artist, track: (name_key(artist), name_key(track)))

    async def close(self) -> None:
        for queue in (self._tags, self._countries, self._releases, self._images, self._listeners, self._tracks):
            await queue.close()

    async def _fetch_image(self, artist: str) -> object:
        url = await deezer.artist_photo(self._http, artist)
        await self._db.save_artist_meta(artist, image_url=url or "", image_at=utcnow())
        return url

    async def _fetch_listeners(self, artist: str) -> object:
        try:
            info = await self._lastfm.artist_info(artist)
        except LastFmError as exc:
            if exc.code != LastFmError.NOT_FOUND:
                return FAILED
            info = {}
        listeners = int((info.get("stats") or {}).get("listeners") or 0)
        await self._db.save_artist_meta(artist, listeners=listeners, listeners_at=utcnow())
        return listeners

    async def _fetch_track(self, artist: str, track: str) -> object:
        """Length in ms from Last.fm, or Deezer when Last.fm doesn't know it (0 = unknown)."""
        duration = 0
        try:
            info = await self._lastfm.track_info(artist, track)
            duration = int(info.get("duration") or 0)
        except LastFmError as exc:
            if exc.code != LastFmError.NOT_FOUND:
                return FAILED
        found = None
        if not duration:
            found = await deezer.find_track(self._http, artist, track)
            duration = found.duration_ms if found else 0
        await self._db.save_track_meta(artist, track, duration, found.url if found else None)
        return duration

    async def _fetch_tags(self, artist: str) -> object:
        try:
            genres = clean_tags(await self._lastfm.artist_tags(artist))
        except LastFmError as exc:
            if exc.code != LastFmError.NOT_FOUND:
                return FAILED  # transient: don't cache
            genres = []
        await self._db.save_artist_meta(artist, tags="|".join(genres), tags_at=utcnow())
        return genres

    async def _musicbrainz(self, url: str, query: str) -> dict | None:
        """A rate-limited MusicBrainz search; None when it's busy (don't cache, try later)."""
        loop = asyncio.get_running_loop()
        async with self._mb_lock:
            delay = self._mb_last + MUSICBRAINZ_SPACING - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                response = await self._http.get(url, params={"query": query, "limit": 5, "fmt": "json"},
                                                headers={"User-Agent": self._user_agent}, timeout=15)
            finally:
                self._mb_last = loop.time()
            if response.status_code == 503:
                self._mb_last += 5  # busy or rate limited: back off
                return None
        response.raise_for_status()
        return response.json()

    async def _fetch_country(self, artist: str) -> object:
        data = await self._musicbrainz(MUSICBRAINZ_URL, f'artist:"{_mb_quote(artist)}"')
        if data is None:
            return FAILED
        found = pick_musicbrainz_artist(artist, data.get("artists") or [])
        await self._db.save_artist_meta(artist, country_code=found[0] if found else "",
                                        country=found[1] if found else "", country_at=utcnow())
        return found

    async def _fetch_release(self, artist: str, album: str) -> object:
        data = await self._musicbrainz(MUSICBRAINZ_RELEASE_GROUPS,
                                       f'releasegroup:"{_mb_quote(album)}" AND artist:"{_mb_quote(artist)}"')
        if data is None:
            return FAILED
        date = pick_release_date(artist, album, data.get("release-groups") or [])
        await self._db.save_album_meta(artist, album, date or "")
        return date

    async def release_date(self, artist: str, album: str, wait: float = 3.0) -> str | None:
        """First release date ('2000', '2000-10' or '2000-10-02'), or None if unknown or still pending."""
        row = await self._db.album_meta(artist, album)
        if row is not None:
            return row.release_date or None
        future = self._releases.request(artist, album)
        try:
            result = await asyncio.wait_for(asyncio.shield(future), timeout=wait)
        except asyncio.TimeoutError:
            return None
        return None if result is FAILED else result

    async def _lookup(self, artists: list[str], queue: _Queue, read, stale_after: timedelta,
                      wait: float, max_new: int) -> Lookup:
        keys = list(dict.fromkeys(name_key(a) for a in artists))
        names = {}
        for artist in artists:
            names.setdefault(name_key(artist), artist)
        cached = await self._db.artist_meta(keys)
        values, missing = {}, []
        now = utcnow()
        for key in keys:
            value, fetched_at = read(cached.get(key))
            if fetched_at is not None:
                values[key] = value
                if fetched_at.tzinfo is None:  # SQLite drops the zone
                    fetched_at = fetched_at.replace(tzinfo=now.tzinfo)
                if now - fetched_at < stale_after:
                    continue
                queue.request(names[key])  # refresh in the background, use the old value now
                continue
            missing.append(key)

        futures = {key: queue.request(names[key]) for key in missing[:max_new]}
        if futures:
            done, _ = await asyncio.wait(futures.values(), timeout=wait)
            for key, future in futures.items():
                if future in done and future.result() is not FAILED:
                    values[key] = future.result()
        return Lookup(values, len(keys) - len(values))

    async def genres(self, artists: list[str], wait: float = 8.0, max_new: int = 300) -> Lookup:
        def read(row):
            if row is None or row.tags_at is None or row.tags is None:
                return None, None
            return [t for t in row.tags.split("|") if t], row.tags_at
        return await self._lookup(artists, self._tags, read, TAGS_STALE_AFTER, wait, max_new)

    async def images(self, artists: list[str], wait: float = 8.0, max_new: int = 100) -> Lookup:
        """Artist photo URLs; None when Deezer has none."""
        def read(row):
            if row is None or row.image_at is None:
                return None, None
            return (row.image_url or None), row.image_at
        return await self._lookup(artists, self._images, read, IMAGES_STALE_AFTER, wait, max_new)

    async def listeners(self, artists: list[str], wait: float = 10.0, max_new: int = 500) -> Lookup:
        """Last.fm listener counts, the iceberg's measure of how well known an artist is."""
        def read(row):
            if row is None or row.listeners_at is None:
                return None, None
            return row.listeners, row.listeners_at
        return await self._lookup(artists, self._listeners, read, LISTENERS_STALE_AFTER, wait, max_new)

    async def durations(self, tracks: list[tuple[str, str]], wait: float = 10.0, max_new: int = 400
                        ) -> tuple[dict[tuple[str, str], int], int]:
        """Track lengths in ms by (artist key, track key), known ones only, plus how many are still pending.
        Tracks are asked for in the given order, so put the most played first."""
        keys = list(dict.fromkeys((name_key(a), name_key(t)) for a, t in tracks))
        names = {}
        for artist, track in tracks:
            names.setdefault((name_key(artist), name_key(track)), (artist, track))
        cached = await self._db.track_meta(keys)
        values = {key: row.duration_ms for key, row in cached.items() if row.duration_ms}
        missing = [key for key in keys if key not in cached]
        futures = {key: self._tracks.request(*names[key]) for key in missing[:max_new]}
        if futures:
            done, _ = await asyncio.wait(futures.values(), timeout=wait)
            for key, future in futures.items():
                if future in done and future.result() is not FAILED:
                    if future.result():
                        values[key] = future.result()
                    missing.remove(key)
        return values, len(missing)

    async def countries(self, artists: list[str], wait: float = 6.0, max_new: int = 300) -> Lookup:
        def read(row):
            if row is None or row.country_at is None:
                return None, None
            return ((row.country_code, row.country) if row.country_code else None), row.country_at
        return await self._lookup(artists, self._countries, read, COUNTRY_STALE_AFTER, wait, max_new)


def by_genre(rows: list[tuple[str, int]], genres: dict[str, list[str]]) -> list[tuple[str, int, list[str]]]:
    """Artist playcounts -> (genre, plays, top artists) per genre, most played first.
    Like FMBot, an artist's plays count fully towards each of its genres."""
    totals: dict[str, list] = {}
    for artist, plays in rows:
        for genre in genres.get(name_key(artist)) or []:
            entry = totals.setdefault(genre_key(genre), [genre, 0, []])
            entry[1] += plays
            entry[2].append(artist)
    return sorted(((g, p, a) for g, p, a in totals.values()), key=lambda e: (-e[1], e[0]))


def by_country(rows: list[tuple[str, int]], countries: dict[str, tuple[str, str] | None]
               ) -> list[tuple[str, str, int, list[str]]]:
    """Artist playcounts -> (code, country, plays, top artists) per country, most played first."""
    totals: dict[str, list] = {}
    for artist, plays in rows:
        found = countries.get(name_key(artist))
        if found:
            entry = totals.setdefault(found[0], [found[0], found[1], 0, []])
            entry[2] += plays
            entry[3].append(artist)
    return sorted((tuple(e) for e in totals.values()), key=lambda e: (-e[2], e[1]))


def match_country(query: str, known: list[tuple[str, str]]) -> tuple[str, str] | None:
    """A typed country ("uk", "GB", "united kingdom", "japan") among (code, name) pairs."""
    wanted = query.strip().casefold()
    code = COUNTRY_ALIASES.get(wanted, wanted.upper() if len(wanted) == 2 else None)
    for found_code, name in known:
        if found_code == code or name.casefold() == wanted:
            return found_code, name
    return next(((c, n) for c, n in known if n.casefold().startswith(wanted)), None) if len(wanted) >= 3 else None
