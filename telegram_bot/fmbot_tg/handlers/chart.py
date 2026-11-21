"""/chart album collage (port of ChartSlashCommands / ChartService, album charts only)."""

import asyncio

import httpx
from aiogram import Router
from aiogram.enums import ChatAction
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, Message

from ..charts import Tile, parse_size, render
from ..context import UserFacingError, resolve_target
from ..db import Database
from ..formatting import esc, user_link
from ..lastfm import LastFmClient
from ..periods import parse_period
from ..stats import Stats

router = Router(name="chart")
_download_sem = asyncio.Semaphore(16)


async def _download(http: httpx.AsyncClient, url: str | None) -> bytes | None:
    if not url:
        return None
    try:
        async with _download_sem:
            response = await http.get(url)
        return response.content if response.status_code == 200 else None
    except httpx.HTTPError:
        return None


@router.message(Command("chart", "c"))
async def chart(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database,
                stats: Stats) -> None:
    period, args = parse_period((command.args or "").split())

    cols, rows = 3, 3
    titles = True
    skip_empty = False
    remaining = []
    for arg in args:
        size = parse_size(arg)
        if size:
            cols, rows = size
        elif arg.lower() in ("notitles", "nt"):
            titles = False
        elif arg.lower() in ("skip", "s", "skipemptyimages"):
            skip_empty = True
        else:
            remaining.append(arg)
    target, _ = await resolve_target(message, remaining, db)

    await message.bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_PHOTO)
    wanted = cols * rows
    # With skip, fetch extra albums so there are enough with covers to fill the grid.
    fetch = min(wanted * 2, 200) if skip_empty else wanted
    top = await stats.top(target, "albums", period, page=1, page_size=fetch)
    if not top.items:
        raise UserFacingError(f"No albums for {user_link(target.lastfm_username)} ({esc(period.label)}).")

    async def cover(item) -> bytes | None:
        # Index-based (time range) charts have no image URLs; look those up separately.
        url = item.image or (await stats.album_image(item.artist, item.name) if period.is_range else None)
        return await _download(lastfm.http, url)

    covers = await asyncio.gather(*(cover(item) for item in top.items))
    tiles = [Tile(image=cover, title=item.name, subtitle=item.artist or "")
             for item, cover in zip(top.items, covers) if cover or not skip_empty][:wanted]
    if not tiles:
        raise UserFacingError("None of those albums have a cover.")
    image = await asyncio.to_thread(render, tiles, cols, rows, titles)

    caption = f"{cols}x{rows} album chart for {user_link(target.lastfm_username)} · {esc(period.label)}"
    await message.reply_photo(BufferedInputFile(image, filename="chart.jpg"), caption=caption)
