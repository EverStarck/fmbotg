"""Async Last.fm API client (port of FMBot.LastFM)."""

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from .cache import TTLCache

log = logging.getLogger(__name__)

API_URL = "https://ws.audioscrobbler.com/2.0/"
AUTH_URL = "https://www.last.fm/api/auth/"

# Last.fm returns this image for anything without artwork (all artist images, nowadays).
PLACEHOLDER_IMAGE_HASH = "2a96cbd8b46e442fc41c2b86b821562f"

# Operation failed / service offline / temporarily unavailable / rate limited
TRANSIENT_ERRORS = {8, 11, 16, 29}

TopKind = Literal["artists", "albums", "tracks"]


class LastFmError(Exception):
    NOT_FOUND = 6
    INVALID_SESSION = 9
    TOKEN_NOT_AUTHORIZED = 14
    TOKEN_EXPIRED = 15
    LOGIN_REQUIRED = 17

    def __init__(self, code: int, message: str):
        super().__init__(f"{message} (code {code})")
        self.code = code
        self.message = message

    @property
    def is_transient(self) -> bool:
        return self.code in TRANSIENT_ERRORS or self.code < 0

    def user_message(self) -> str:
        if self.code == self.NOT_FOUND:
            return "Couldn't find that on Last.fm."
        if self.code == self.LOGIN_REQUIRED:
            return "That Last.fm profile has its recent listening hidden."
        if self.code == self.INVALID_SESSION:
            return "Your Last.fm session is no longer valid. Use /login to reconnect."
        if self.code == 29:
            return "Last.fm is rate limiting us right now. Try again in a minute."
        if self.is_transient:
            return "Last.fm is having issues right now. Please try again later."
        return f"Last.fm returned an error: {self.message}"


def sign(params: dict[str, str], secret: str) -> str:
    """Last.fm api_sig: md5 of sorted key+value pairs (excluding format/callback) plus the secret."""
    payload = "".join(f"{key}{params[key]}" for key in sorted(params) if key not in ("format", "callback"))
    return hashlib.md5((payload + secret).encode("utf-8")).hexdigest()


def as_list(value: Any) -> list:
    """Last.fm returns a bare object instead of a one-element list when there is a single result."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def pick_image(images: Any) -> str | None:
    by_size = {img.get("size"): img.get("#text") for img in as_list(images) if isinstance(img, dict)}
    for size in ("extralarge", "mega", "large", "medium", "small"):
        url = by_size.get(size)
        if url and PLACEHOLDER_IMAGE_HASH not in url:
            return url
    return None


def _text(value: Any) -> str:
    if isinstance(value, dict):
        return value.get("#text") or value.get("name") or ""
    return value or ""


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


@dataclass(frozen=True)
class Scrobble:
    track: str
    artist: str
    album: str
    url: str
    image: str | None
    now_playing: bool
    timestamp: int | None
    loved: bool = False


@dataclass(frozen=True)
class RecentTracks:
    tracks: list[Scrobble]
    total: int


@dataclass(frozen=True)
class TopItem:
    name: str
    artist: str | None
    playcount: int
    url: str
    image: str | None


@dataclass(frozen=True)
class TopList:
    items: list[TopItem]
    total: int
    page: int
    total_pages: int


def parse_recent_tracks(data: dict) -> RecentTracks:
    root = data.get("recenttracks") or {}
    tracks = []
    for item in as_list(root.get("track")):
        attr = item.get("@attr") or {}
        date = item.get("date") or {}
        tracks.append(
            Scrobble(
                track=item.get("name", ""),
                artist=_text(item.get("artist")),
                album=_text(item.get("album")),
                url=item.get("url", ""),
                image=pick_image(item.get("image")),
                now_playing=str(attr.get("nowplaying", "")).lower() == "true",
                timestamp=_int(date.get("uts")) or None,
                loved=str(item.get("loved", "0")) == "1",
            )
        )
    total = _int((root.get("@attr") or {}).get("total"))
    return RecentTracks(tracks=tracks, total=total)


def parse_top(data: dict, kind: TopKind) -> TopList:
    root = data.get(f"top{kind}") or {}
    attr = root.get("@attr") or {}
    items = [
        TopItem(
            name=item.get("name", ""),
            artist=_text(item.get("artist")) if kind != "artists" else None,
            playcount=_int(item.get("playcount")),
            url=item.get("url", ""),
            image=pick_image(item.get("image")),
        )
        for item in as_list(root.get(kind[:-1]))
    ]
    return TopList(
        items=items,
        total=_int(attr.get("total")),
        page=_int(attr.get("page")) or 1,
        total_pages=_int(attr.get("totalPages")) or 1,
    )


class LastFmClient:
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        private_key: str | None = None,
        http: httpx.AsyncClient | None = None,
        max_concurrency: int = 8,
    ):
        self.api_key = api_key
        self._secret = api_secret
        self._private_key = private_key
        self._http = http or httpx.AsyncClient(timeout=15)
        self._sem = asyncio.Semaphore(max_concurrency)
        self._playcounts = TTLCache(ttl_seconds=300)

    @property
    def http(self) -> httpx.AsyncClient:
        return self._http

    async def close(self) -> None:
        await self._http.aclose()

    def auth_url(self, token: str) -> str:
        return f"{AUTH_URL}?api_key={self.api_key}&token={token}"

    async def call(self, method: str, *, signed: bool = False, retries: int = 2, **params: Any) -> dict:
        query = {key: str(value) for key, value in params.items() if value is not None}
        signed = signed or "sk" in query
        query["method"] = method
        query["api_key"] = self.api_key if signed or not self._private_key else self._private_key
        if signed:
            query["api_sig"] = sign(query, self._secret)
        query["format"] = "json"

        last_error: LastFmError | None = None
        for attempt in range(retries + 1):
            if attempt:
                await asyncio.sleep(0.6 * attempt)
            try:
                async with self._sem:
                    response = await self._http.get(API_URL, params=query)
            except httpx.HTTPError as exc:
                log.warning("Last.fm %s transport error: %s", method, exc)
                last_error = LastFmError(-1, "Could not reach Last.fm")
                continue

            if response.status_code >= 500:
                last_error = LastFmError(-response.status_code, f"Last.fm server error {response.status_code}")
                continue

            try:
                data = response.json()
            except ValueError:
                last_error = LastFmError(-2, "Last.fm returned an invalid response")
                continue

            if isinstance(data, dict) and "error" in data:
                error = LastFmError(_int(data["error"]), data.get("message", "Unknown error"))
                if error.is_transient:
                    last_error = error
                    continue
                raise error
            return data

        assert last_error is not None
        raise last_error

    # --- auth ---------------------------------------------------------------------------

    async def get_token(self) -> str:
        data = await self.call("auth.getToken", signed=True)
        return data["token"]

    async def get_session(self, token: str) -> tuple[str, str]:
        """Returns (username, session_key). Raises code 14 while the user hasn't approved yet."""
        data = await self.call("auth.getSession", signed=True, retries=0, token=token)
        session = data["session"]
        return session["name"], session["key"]

    # --- user ---------------------------------------------------------------------------

    async def user_info(self, username: str) -> dict:
        return (await self.call("user.getInfo", user=username))["user"]

    async def recent_tracks(self, username: str, limit: int = 10, session_key: str | None = None) -> RecentTracks:
        data = await self.call("user.getRecentTracks", user=username, limit=limit, extended=1, sk=session_key)
        return parse_recent_tracks(data)

    async def top(self, username: str, kind: TopKind, period: str, limit: int = 10, page: int = 1) -> TopList:
        method = f"user.getTop{kind.capitalize()}"
        data = await self.call(method, user=username, period=period, limit=limit, page=page)
        return parse_top(data, kind)

    # --- metadata -----------------------------------------------------------------------

    async def artist_info(self, artist: str, username: str | None = None) -> dict:
        data = await self.call("artist.getInfo", artist=artist, username=username, autocorrect=1)
        return data["artist"]

    async def album_info(self, artist: str, album: str, username: str | None = None) -> dict:
        data = await self.call("album.getInfo", artist=artist, album=album, username=username, autocorrect=1)
        return data["album"]

    async def track_info(self, artist: str, track: str, username: str | None = None) -> dict:
        data = await self.call("track.getInfo", artist=artist, track=track, username=username, autocorrect=1)
        return data["track"]

    async def search_album(self, query: str) -> tuple[str, str] | None:
        data = await self.call("album.search", album=query, limit=1)
        matches = as_list(((data.get("results") or {}).get("albummatches") or {}).get("album"))
        return (matches[0]["artist"], matches[0]["name"]) if matches else None

    async def search_track(self, query: str) -> tuple[str, str] | None:
        data = await self.call("track.search", track=query, limit=1)
        matches = as_list(((data.get("results") or {}).get("trackmatches") or {}).get("track"))
        return (matches[0]["artist"], matches[0]["name"]) if matches else None

    async def user_playcount(self, kind: str, names: tuple[str, ...], username: str) -> int:
        """Playcount of one user for an artist (names=(artist,)), album or track ((artist, title))."""
        key = (kind, tuple(n.lower() for n in names), username.lower())
        cached = self._playcounts.get(key)
        if cached is not None:
            return cached
        try:
            if kind == "artist":
                info = await self.artist_info(names[0], username)
                count = _int((info.get("stats") or {}).get("userplaycount"))
            elif kind == "album":
                count = _int((await self.album_info(names[0], names[1], username)).get("userplaycount"))
            else:
                count = _int((await self.track_info(names[0], names[1], username)).get("userplaycount"))
        except LastFmError as exc:
            if exc.code != LastFmError.NOT_FOUND:
                raise
            count = 0
        self._playcounts.set(key, count)
        return count
