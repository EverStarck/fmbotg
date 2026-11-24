"""Album and artist collages as JPEG bytes (port of ChartService), shared by /chart and /recap."""

import asyncio

import httpx

from .charts import Tile, render
from .domain import Target, UserFacingError
from .formatting import esc, user_link
from .lastfm import LastFmClient
from .metadata import Metadata
from .moderation import Moderation
from .names import name_key
from .periods import Period
from .stats import Stats

_download_sem = asyncio.Semaphore(16)


async def download(http: httpx.AsyncClient, url: str | None) -> bytes | None:
    if not url:
        return None
    try:
        async with _download_sem:
            response = await http.get(url)
        return response.content if response.status_code == 200 else None
    except httpx.HTTPError:
        return None


async def album_chart(stats: Stats, lastfm: LastFmClient, moderation: Moderation, target: Target, period: Period,
                      cols: int, rows: int, titles: bool = True, skip_empty: bool = False) -> bytes:
    """Render an album collage as JPEG bytes (shared by /chart and /recap)."""
    wanted = cols * rows
    # With skip, fetch extra albums so there are enough with covers to fill the grid.
    fetch = min(wanted * 2, 200) if skip_empty else wanted
    top = await stats.top(target, "albums", period, page=1, page_size=fetch)
    if not top.items:
        raise UserFacingError(f"No albums for {user_link(target.lastfm_username)} ({esc(period.label)}).")

    async def cover(item) -> bytes | None:
        if moderation.is_censored(item.artist, item.name):
            return None
        # Index-based (time range) charts have no image URLs; look those up separately.
        url = item.image or (await stats.album_image(item.artist, item.name) if period.is_range else None)
        return await download(lastfm.http, url)

    covers = await asyncio.gather(*(cover(item) for item in top.items))
    tiles = [Tile(image=cover, title=item.name, subtitle=item.artist or "")
             for item, cover in zip(top.items, covers) if cover or not skip_empty][:wanted]
    if not tiles:
        raise UserFacingError("None of those albums have a cover.")
    return await asyncio.to_thread(render, tiles, cols, rows, titles)


async def artist_chart(stats: Stats, lastfm: LastFmClient, metadata: Metadata, moderation: Moderation, target: Target,
                       period: Period, cols: int, rows: int, titles: bool = True, skip_empty: bool = False) -> bytes:
    """Artist photo collage (FMBot's artist charts, with photos from Deezer instead of Spotify)."""
    wanted = cols * rows
    top = await stats.top(target, "artists", period, page=1, page_size=min(wanted * 2, 200) if skip_empty else wanted)
    if not top.items:
        raise UserFacingError(f"No artists for {user_link(target.lastfm_username)} ({esc(period.label)}).")
    photos = (await metadata.images([item.name for item in top.items], wait=12)).values
    images = await asyncio.gather(*(
        download(lastfm.http, None if moderation.is_censored(item.name) else photos.get(name_key(item.name)))
        for item in top.items))
    tiles = [Tile(image=image, title=item.name, subtitle=f"{item.playcount:,} plays")
             for item, image in zip(top.items, images) if image or not skip_empty][:wanted]
    if not tiles:
        raise UserFacingError("None of those artists have a photo.")
    return await asyncio.to_thread(render, tiles, cols, rows, titles)
