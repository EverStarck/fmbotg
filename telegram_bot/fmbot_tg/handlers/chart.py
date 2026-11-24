"""/chart album or artist collage (port of ChartSlashCommands / ChartService)."""

from aiogram import Router
from aiogram.enums import ChatAction
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, Message

from ..chart_service import album_chart, artist_chart
from ..charts import parse_size
from ..context import resolve_target
from ..db import Database
from ..formatting import esc, user_link
from ..lastfm import LastFmClient
from ..metadata import Metadata
from ..moderation import Moderation
from ..periods import parse_period
from ..stats import Stats

router = Router(name="chart")
@router.message(Command("chart", "c"))
async def chart(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database,
                stats: Stats, metadata: Metadata, moderation: Moderation) -> None:
    period, args = parse_period((command.args or "").split())

    cols, rows = 3, 3
    titles = True
    skip_empty = False
    artists = False
    remaining = []
    for arg in args:
        size = parse_size(arg)
        if size:
            cols, rows = size
        elif arg.lower() in ("notitles", "nt"):
            titles = False
        elif arg.lower() in ("skip", "s", "skipemptyimages"):
            skip_empty = True
        elif arg.lower() in ("artists", "artist", "ar"):
            artists = True
        else:
            remaining.append(arg)
    target, _ = await resolve_target(message, remaining, db)

    await message.bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_PHOTO)
    if artists:
        image = await artist_chart(stats, lastfm, metadata, moderation, target, period, cols, rows, titles, skip_empty)
    else:
        image = await album_chart(stats, lastfm, moderation, target, period, cols, rows, titles, skip_empty)
    kind = "artist" if artists else "album"
    caption = f"{cols}x{rows} {kind} chart for {user_link(target.lastfm_username)} · {esc(period.label)}"
    await message.reply_photo(BufferedInputFile(image, filename="chart.jpg"), caption=caption)
