"""Deezer's public API (no key needed): artist photos, track lengths and links.

Stands in for the Spotify data FMBot uses, since Spotify's API now needs a Premium account
for new apps and no longer returns popularity.
"""

from dataclasses import dataclass

import httpx

from .names import normalize_answer

API = "https://api.deezer.com"


@dataclass(frozen=True)
class DeezerTrack:
    title: str
    artist: str
    duration_ms: int
    url: str
    preview: str | None


def _same(a: str, b: str) -> bool:
    return normalize_answer(a) == normalize_answer(b)


async def _search(http: httpx.AsyncClient, path: str, query: str, limit: int) -> list[dict]:
    response = await http.get(f"{API}/{path}", params={"q": query, "limit": limit}, timeout=15)
    response.raise_for_status()
    data = response.json()
    if "error" in data:
        raise httpx.HTTPError(f"Deezer error: {data['error']}")
    return data.get("data") or []


async def artist_photo(http: httpx.AsyncClient, artist: str) -> str | None:
    """The photo of the artist with exactly this name, if Deezer has a real one."""
    for found in await _search(http, "search/artist", artist, 5):
        if _same(found.get("name", ""), artist):
            picture = found.get("picture_xl") or found.get("picture_big")
            # Artists without a photo get a placeholder whose URL has an empty hash
            return picture if picture and "/artist//" not in picture else None
    return None


async def find_track(http: httpx.AsyncClient, artist: str, track: str) -> DeezerTrack | None:
    """The track by this artist, preferring an exact title over versions like '(Live)'."""
    results = [r for r in await _search(http, "search", f"{artist} {track}", 10)
               if _same((r.get("artist") or {}).get("name", ""), artist)]
    exact = [r for r in results if _same(r.get("title", ""), track) or _same(r.get("title_short", ""), track)]
    best = (exact or [r for r in results if normalize_answer(track) in normalize_answer(r.get("title", ""))] or [None])[0]
    if best is None:
        return None
    return DeezerTrack(best.get("title", track), best["artist"]["name"], int(best.get("duration") or 0) * 1000,
                       best.get("link", ""), best.get("preview") or None)
