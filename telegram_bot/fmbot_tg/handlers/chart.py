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
from ..periods import UnsupportedPeriod, parse_period

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
async def chart(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database) -> None:
    args = (command.args or "").split()
    try:
        period, args = parse_period(args)
    except UnsupportedPeriod as exc:
        raise UserFacingError(esc(str(exc))) from exc

    cols, rows = 3, 3
    titles = True
    remaining = []
    for arg in args:
        size = parse_size(arg)
        if size:
            cols, rows = size
        elif arg.lower() in ("notitles", "nt"):
            titles = False
        else:
            remaining.append(arg)
    target, _ = await resolve_target(message, remaining, db)

    await message.bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_PHOTO)
    top = await lastfm.top(target.lastfm_username, "albums", period.api, limit=cols * rows)
    if not top.items:
        raise UserFacingError(f"No {period.label} albums for {user_link(target.lastfm_username)}.")

    covers = await asyncio.gather(*(_download(lastfm.http, item.image) for item in top.items))
    tiles = [Tile(image=cover, title=item.name, subtitle=item.artist or "")
             for item, cover in zip(top.items, covers)]
    image = await asyncio.to_thread(render, tiles, cols, rows, titles)

    caption = f"{cols}x{rows} {period.label} album chart for {user_link(target.lastfm_username)}"
    await message.reply_photo(BufferedInputFile(image, filename="chart.jpg"), caption=caption)
