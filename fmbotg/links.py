"""Spotify, YouTube and Deezer links for a track (port of SpotifyService/YoutubeService lookups).

Deezer is always exact (free API). Spotify and YouTube need keys for exact links; without them
the bot links to a search, which opens the right results in the app.
"""

import base64
import time
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from . import deezer
from .cache import TTLCache


@dataclass(frozen=True)
class TrackLinks:
    spotify: str
    spotify_exact: bool
    youtube: str
    youtube_exact: bool
    deezer: str | None
    preview: str | None  # 30-second Deezer preview


class MusicLinks:
    def __init__(self, http: httpx.AsyncClient, spotify_id: str | None, spotify_secret: str | None,
                 youtube_key: str | None):
        self._http = http
        self._spotify_id, self._spotify_secret = spotify_id, spotify_secret
        self._youtube_key = youtube_key
        self._token: tuple[str, float] | None = None
        self._cache = TTLCache(ttl_seconds=6 * 3600, max_items=5_000)

    async def _spotify_token(self) -> str:
        if self._token and self._token[1] > time.time() + 60:
            return self._token[0]
        basic = base64.b64encode(f"{self._spotify_id}:{self._spotify_secret}".encode()).decode()
        response = await self._http.post("https://accounts.spotify.com/api/token", data={"grant_type": "client_credentials"},
                                         headers={"Authorization": f"Basic {basic}"}, timeout=15)
        response.raise_for_status()
        data = response.json()
        self._token = (data["access_token"], time.time() + int(data.get("expires_in", 3600)))
        return self._token[0]

    async def _spotify(self, artist: str, track: str) -> str | None:
        if not (self._spotify_id and self._spotify_secret):
            return None
        try:
            response = await self._http.get(
                "https://api.spotify.com/v1/search", params={"q": f"track:{track} artist:{artist}", "type": "track",
                                                              "limit": 1},
                headers={"Authorization": f"Bearer {await self._spotify_token()}"}, timeout=15)
            response.raise_for_status()
            items = (response.json().get("tracks") or {}).get("items") or []
            return items[0]["external_urls"]["spotify"] if items else None
        except (httpx.HTTPError, KeyError, ValueError):
            return None

    async def _youtube(self, artist: str, track: str) -> str | None:
        if not self._youtube_key:
            return None
        try:
            response = await self._http.get(
                "https://www.googleapis.com/youtube/v3/search",
                params={"part": "snippet", "q": f"{artist} {track}", "type": "video", "maxResults": 1,
                        "key": self._youtube_key}, timeout=15)
            response.raise_for_status()
            items = response.json().get("items") or []
            return f"https://www.youtube.com/watch?v={items[0]['id']['videoId']}" if items else None
        except (httpx.HTTPError, KeyError, ValueError):
            return None

    async def for_track(self, artist: str, track: str) -> TrackLinks:
        key = (artist.lower(), track.lower())
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        query = quote(f"{artist} {track}")
        spotify, youtube = await self._spotify(artist, track), await self._youtube(artist, track)
        try:
            found = await deezer.find_track(self._http, artist, track)
        except httpx.HTTPError:
            found = None
        links = TrackLinks(
            spotify or f"https://open.spotify.com/search/{query}", spotify is not None,
            youtube or f"https://www.youtube.com/results?search_query={quote(f'{artist} {track}')}", youtube is not None,
            found.url if found else None, found.preview if found else None,
        )
        self._cache.set(key, links)
        return links
