"""/fm and /recent (port of PlaySlashCommands / PlayBuilders.NowPlaying and RecentTracks)."""

import asyncio

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from ..context import UserFacingError, resolve_target
from ..db import Database
from ..formatting import TELEGRAM_CAPTION_LIMIT, ago, esc, link, num, plural, truncate, user_link
from ..lastfm import LastFmClient

router = Router(name="play")


@router.message(Command("fm", "np", "nowplaying"))
async def now_playing(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database) -> None:
    target, _ = await resolve_target(message, (command.args or "").split(), db)
    user = target.lastfm_username
    recent = await lastfm.recent_tracks(user, limit=2, session_key=target.session_key)
    if not recent.tracks:
        raise UserFacingError(f"{user_link(user)} has no scrobbles yet.")

    current = recent.tracks[0]
    previous = recent.tracks[1] if len(recent.tracks) > 1 else None

    track_plays, artist_plays = await asyncio.gather(
        lastfm.user_playcount("track", (current.artist, current.track), user),
        lastfm.user_playcount("artist", (current.artist,), user),
        return_exceptions=True,
    )

    heading = "🎧 Now playing" if current.now_playing else "Last track"
    lines = [
        f"{heading} · {user_link(user)}",
        "",
        f"<b>{link(current.track, current.url)}</b>",
        f"by <b>{esc(current.artist)}</b>" + (f" · <i>{esc(current.album)}</i>" if current.album else ""),
    ]
    if not current.now_playing and current.timestamp:
        lines.append(f"<i>{ago(current.timestamp)}</i>")
    if previous and current.now_playing:
        lines += ["", f"<i>Previous:</i> {esc(previous.track)} — {esc(previous.artist)}"]

    stats = []
    if isinstance(track_plays, int):
        stats.append(plural(track_plays, "play"))
    if isinstance(artist_plays, int):
        stats.append(f"{plural(artist_plays, 'artist scrobble')}")
    stats.append(f"{plural(recent.total, 'total scrobble')}")
    lines += ["", "<code>" + " · ".join(stats) + "</code>"]
    if current.loved:
        lines[-1] = "❤️ " + lines[-1]

    text = "\n".join(lines)
    if current.image:
        try:
            await message.reply_photo(current.image, caption=truncate(text, TELEGRAM_CAPTION_LIMIT))
            return
        except TelegramBadRequest:
            pass  # Telegram couldn't fetch the cover; fall back to text
    await message.reply(text)


@router.message(Command("recent", "recenttracks", "rt"))
async def recent(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database) -> None:
    args = (command.args or "").split()
    amount = 10
    for arg in list(args):
        if arg.isdigit():
            amount = max(1, min(int(arg), 25))
            args.remove(arg)
            break
    target, _ = await resolve_target(message, args, db)

    result = await lastfm.recent_tracks(target.lastfm_username, limit=amount, session_key=target.session_key)
    if not result.tracks:
        raise UserFacingError(f"{user_link(target.lastfm_username)} has no scrobbles yet.")

    lines = [f"<b>Latest tracks for {user_link(target.lastfm_username)}</b>", ""]
    # Last.fm adds the now playing track on top of `limit`
    for scrobble in result.tracks[:amount]:
        prefix = "▶️" if scrobble.now_playing else "•"
        when = "" if scrobble.now_playing else f" <i>· {ago(scrobble.timestamp)}</i>"
        lines.append(f"{prefix} {link(scrobble.track, scrobble.url)} — {esc(scrobble.artist)}{when}")
    lines += ["", f"<code>{num(result.total)} total scrobbles</code>"]
    await message.reply("\n".join(lines))
