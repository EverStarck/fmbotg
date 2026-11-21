"""/artist, /album, /track (port of ArtistBuilders/AlbumBuilders/TrackBuilders info commands)."""

import asyncio
import re

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from ..context import UserFacingError, resolve_subject, resolve_target
from ..db import Database, name_key
from ..formatting import (
    TELEGRAM_CAPTION_LIMIT, TELEGRAM_TEXT_LIMIT, clean_wiki, duration, esc, link, num, plural, truncate,
    user_link,
)
from ..lastfm import LastFmClient, as_list, pick_image
from ..stats import Stats

router = Router(name="info")
MAX_LIVE_TRACKS = 30


def _tags(info: dict) -> str:
    tags = [t.get("name", "") for t in as_list((info.get("tags") or info.get("toptags") or {}).get("tag"))]
    return " · ".join(esc(t.lower()) for t in tags[:6] if t)


async def _send(message: Message, text: str, image: str | None) -> None:
    if image:
        try:
            await message.reply_photo(image, caption=truncate(text, TELEGRAM_CAPTION_LIMIT))
            return
        except TelegramBadRequest:
            pass
    await message.reply(truncate(text, TELEGRAM_TEXT_LIMIT))


@router.message(Command("artist", "a", "artistinfo"))
async def artist(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database) -> None:
    target, rest = await resolve_target(message, (command.args or "").split(), db)
    (name,) = await resolve_subject("artist", " ".join(rest), target, lastfm)
    info = await lastfm.artist_info(name, target.lastfm_username)
    stats = info.get("stats") or {}

    lines = [f"<b>{link(info['name'], info.get('url', ''))}</b>", ""]
    lines.append(f"{user_link(target.lastfm_username)}: <b>{num(int(stats.get('userplaycount') or 0))}</b> plays")
    lines.append(f"Last.fm: {num(int(stats.get('listeners') or 0))} listeners · "
                 f"{num(int(stats.get('playcount') or 0))} plays")
    if tags := _tags(info):
        lines += ["", f"<i>{tags}</i>"]
    if bio := clean_wiki((info.get("bio") or {}).get("summary")):
        lines += ["", bio]
    similar = [a.get("name", "") for a in as_list((info.get("similar") or {}).get("artist"))][:5]
    if similar:
        lines += ["", "Similar: " + ", ".join(esc(s) for s in similar)]
    await message.reply(truncate("\n".join(lines), TELEGRAM_TEXT_LIMIT))


@router.message(Command("album", "ab", "albuminfo"))
async def album(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database) -> None:
    target, rest = await resolve_target(message, (command.args or "").split(), db)
    artist_name, album_name = await resolve_subject("album", " ".join(rest), target, lastfm)
    info = await lastfm.album_info(artist_name, album_name, target.lastfm_username)

    tracks = as_list((info.get("tracks") or {}).get("track"))
    total_ms = sum(int(t.get("duration") or 0) for t in tracks) * 1000

    lines = [f"<b>{link(info['name'], info.get('url', ''))}</b>", f"by <b>{esc(info.get('artist', ''))}</b>", ""]
    lines.append(f"{user_link(target.lastfm_username)}: <b>{num(int(info.get('userplaycount') or 0))}</b> plays")
    lines.append(f"Last.fm: {num(int(info.get('listeners') or 0))} listeners · "
                 f"{num(int(info.get('playcount') or 0))} plays")
    if tracks:
        lines.append(f"{len(tracks)} tracks" + (f" · {duration(total_ms)}" if total_ms else ""))
    if tags := _tags(info):
        lines += ["", f"<i>{tags}</i>"]
    if wiki := clean_wiki((info.get("wiki") or {}).get("summary"), limit=300):
        lines += ["", wiki]
    await _send(message, "\n".join(lines), pick_image(info.get("image")))


@router.message(Command("track", "tr", "trackinfo"))
async def track(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database) -> None:
    target, rest = await resolve_target(message, (command.args or "").split(), db)
    artist_name, track_name = await resolve_subject("track", " ".join(rest), target, lastfm)
    info = await lastfm.track_info(artist_name, track_name, target.lastfm_username)

    album_info = info.get("album") or {}
    lines = [f"<b>{link(info['name'], info.get('url', ''))}</b>",
             f"by <b>{esc((info.get('artist') or {}).get('name', artist_name))}</b>"
             + (f" · <i>{esc(album_info['title'])}</i>" if album_info.get("title") else ""), ""]
    plays_line = f"{user_link(target.lastfm_username)}: <b>{num(int(info.get('userplaycount') or 0))}</b> plays"
    if str(info.get("userloved")) == "1":
        plays_line += " · ❤️ loved"
    lines.append(plays_line)
    lines.append(f"Last.fm: {num(int(info.get('listeners') or 0))} listeners · "
                 f"{num(int(info.get('playcount') or 0))} plays")
    if int(info.get("duration") or 0):
        lines.append(f"Duration: {duration(int(info['duration']))}")
    if tags := _tags(info):
        lines += ["", f"<i>{tags}</i>"]
    await _send(message, "\n".join(lines), pick_image(album_info.get("image")))


@router.message(Command("albumtracks", "abt", "tracklist"))
async def album_tracks(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database,
                       stats: Stats) -> None:
    """Track list with the user's plays per track (port of AlbumBuilders.AlbumTracksAsync)."""
    target, rest = await resolve_target(message, (command.args or "").split(), db)
    artist_name, album_name = await resolve_subject("album", " ".join(rest), target, lastfm)
    info = await lastfm.album_info(artist_name, album_name)
    tracks = as_list((info.get("tracks") or {}).get("track"))
    if not tracks:
        raise UserFacingError(f"Last.fm has no track list for <b>{esc(info.get('name', album_name))}</b>.")
    artist = info.get("artist", artist_name)
    names = [t.get("name", "") for t in tracks]

    if target.indexed and target.user_id is not None:
        await stats.require_index(target)
        by_key = await db.track_counts(target.user_id, artist, names)
        counts = [by_key.get(name_key(n), 0) for n in names]
    else:
        # One Last.fm call per track; fine for normal albums, capped for box sets.
        results = await asyncio.gather(
            *(lastfm.user_playcount("track", (artist, n), target.lastfm_username) for n in names[:MAX_LIVE_TRACKS]),
            return_exceptions=True,
        )
        counts = [r if isinstance(r, int) else 0 for r in results] + [0] * (len(names) - MAX_LIVE_TRACKS)

    lines = [f"<b>{link(info.get('name', album_name), info.get('url', ''))}</b> by <b>{esc(artist)}</b>",
             f"Track plays for {user_link(target.lastfm_username)}", ""]
    for i, (track, count) in enumerate(zip(tracks, counts), 1):
        length = int(track.get("duration") or 0)
        extra = f" <i>{duration(length * 1000)}</i>" if length else ""
        lines.append(f"{i}. {esc(track.get('name', ''))}{extra} · <b>{num(count)}</b>")
    lines += ["", f"<code>{plural(sum(counts), 'play')} total</code>"]
    await message.reply(truncate("\n".join(lines), TELEGRAM_TEXT_LIMIT))


@router.message(Command("cover", "co", "albumcover"))
async def cover(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database) -> None:
    """Full-size album cover (port of AlbumBuilders.CoverAsync)."""
    target, rest = await resolve_target(message, (command.args or "").split(), db)
    artist_name, album_name = await resolve_subject("album", " ".join(rest), target, lastfm)
    info = await lastfm.album_info(artist_name, album_name)
    image = pick_image(info.get("image"))
    if not image:
        raise UserFacingError(f"No cover found for <b>{esc(info.get('name', album_name))}</b>.")
    caption = f"<b>{link(info.get('name', album_name), info.get('url', ''))}</b> by {esc(info.get('artist', artist_name))}"
    try:
        await message.reply_photo(full_size_image(image), caption=caption)
    except TelegramBadRequest:
        await message.reply_photo(image, caption=caption)  # original too large or unavailable


def full_size_image(url: str) -> str:
    """Last.fm image URLs carry a size segment (…/i/u/300x300/hash.jpg); removing it gives the original."""
    return re.sub(r"/i/u/\d+x\d+/", "/i/u/", url)
