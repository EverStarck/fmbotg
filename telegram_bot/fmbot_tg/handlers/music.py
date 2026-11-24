"""Commands built on data beyond Last.fm (phase 5): /iceberg, /playtime, /timeleaderboard, /lyrics,
/spotify, /youtube, /deezer and /links (ports of ArtistBuilders.GetIceberg, the playtime commands,
the lyrics command and the Spotify/YouTube search commands)."""

import asyncio
import time
from collections import Counter

import httpx
from aiogram import Router
from aiogram.enums import ChatAction
from aiogram.filters import Command, CommandObject
from aiogram.types import BufferedInputFile, LinkPreviewOptions, Message

from ..charts import render_iceberg
from ..config import Settings
from ..context import Target, UserFacingError, require_group, resolve_subject, resolve_target
from ..db import Database, name_key
from ..formatting import TELEGRAM_TEXT_LIMIT, esc, link, plural, truncate, user_link
from ..lastfm import LastFmClient
from ..links import MusicLinks
from ..metadata import Metadata
from ..periods import ALL_TIME, WEEKLY, Period, parse_period
from ..playtime import estimate_note, hours, listening_time
from ..stats import Stats

router = Router(name="music")

# --- iceberg ---------------------------------------------------------------------------------

# Last.fm listeners per depth band, standing in for the Spotify popularity FMBot uses
ICEBERG_TIERS = [
    (3_000_000, "3M+ listeners"), (1_000_000, "1M+"), (500_000, "500k+"), (250_000, "250k+"), (100_000, "100k+"),
    (50_000, "50k+"), (20_000, "20k+"), (5_000, "5k+"), (1_000, "1k+"), (0, "Under 1k"),
]
ICEBERG_ARTISTS = 500


def iceberg_sections(rows: list[tuple[str, int]], listeners: dict[str, int]) -> list[tuple[str, list[str]]]:
    """Up to 5 of your most played artists per band. Like FMBot, artists need 15 plays when you have
    over 100 of those, else 5, else any play at all, so small libraries still get an iceberg."""
    plenty = sum(1 for _, plays in rows if plays >= 15)
    minimum = 15 if plenty > 100 else 5 if sum(1 for _, p in rows if p >= 5) >= 20 else 1
    bands: list[list[str]] = [[] for _ in ICEBERG_TIERS]
    for artist, plays in sorted(rows, key=lambda r: -r[1]):
        count = listeners.get(name_key(artist))
        if count is None or plays < minimum:
            continue
        band = next(i for i, (low, _) in enumerate(ICEBERG_TIERS) if count >= low)
        if len(bands[band]) < 5:
            bands[band].append(artist)
    return [(label, names) for (_, label), names in zip(ICEBERG_TIERS, bands)]


@router.message(Command("iceberg", "ice", "icebergify", "berg"))
async def iceberg(message: Message, command: CommandObject, db: Database, stats: Stats, metadata: Metadata) -> None:
    period, args = parse_period((command.args or "").split(), default=ALL_TIME)
    target, _ = await resolve_target(message, args, db)
    await message.bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_PHOTO)
    top = await stats.top(target, "artists", period, page=1, page_size=ICEBERG_ARTISTS)
    rows = [(item.name, item.playcount) for item in top.items]
    if not rows:
        raise UserFacingError(f"No artists for {user_link(target.lastfm_username)} ({esc(period.label)}).")
    lookup = await metadata.listeners([a for a, _ in rows], wait=12)
    sections = iceberg_sections(rows, {k: v for k, v in lookup.values.items() if v is not None})
    image = await asyncio.to_thread(render_iceberg, target.lastfm_username, period.label, sections)
    caption = f"Iceberg for {user_link(target.lastfm_username)} · {esc(period.label)}"
    if lookup.pending:
        caption += (f"\n<i>Still looking up {plural(lookup.pending, 'artist')}; run it again in a minute "
                    "for the full picture.</i>")
    await message.reply_photo(BufferedInputFile(image, "iceberg.png"), caption=caption)


# --- listening time --------------------------------------------------------------------------

async def _track_counts(stats: Stats, db: Database, target: Target, period: Period) -> list[tuple[str, str, int]]:
    if target.indexed and target.user_id is not None:
        await stats.require_index(target)
        from_ts, to_ts = (0, int(time.time()) + 1) if period == ALL_TIME else period.bounds(tz=target.tz)
        return [(a, t, c) for _, a, t, c in await db.track_playcounts([target.user_id], from_ts, to_ts)]
    if period == ALL_TIME:
        await stats.require_index(target)
    from_ts, to_ts = period.bounds(tz=target.tz)
    counts: Counter = Counter()
    names = {}
    for play in await stats.plays_window(target, from_ts, to_ts):
        key = (name_key(play.artist), name_key(play.track))
        counts[key] += 1
        names.setdefault(key, (play.artist, play.track))
    return [(*names[key], plays) for key, plays in counts.items()]


@router.message(Command("playtime", "pt", "timelistened", "listeningtime", "time"))
async def playtime(message: Message, command: CommandObject, db: Database, stats: Stats, metadata: Metadata) -> None:
    period, args = parse_period((command.args or "").split(), default=WEEKLY)
    target, _ = await resolve_target(message, args, db)
    counts = await _track_counts(stats, db, target, period)
    if not counts:
        raise UserFacingError(f"No plays for {user_link(target.lastfm_username)} ({esc(period.label)}).")
    result = await listening_time(metadata, counts)

    lines = [f"<b>Listening time for {user_link(target.lastfm_username)}</b> · {esc(period.label)}", ""]
    summary = f"⏱ <b>{hours(result.total_ms)}</b> over {plural(result.plays, 'play')}"
    if period != ALL_TIME:
        from_ts, to_ts = period.bounds(tz=target.tz)
        days = max(1.0, (to_ts - from_ts) / 86400)
        summary += f" · {hours(int(result.total_ms / days))} a day"
    lines.append(summary)

    by_artist: Counter = Counter()
    shown_name = {}
    for item in result.items:
        by_artist[name_key(item.artist)] += item.ms
        shown_name.setdefault(name_key(item.artist), item.artist)
    lines += ["", "<b>Artists</b>"]
    lines += [f"{i}. {esc(shown_name[key])} · {hours(ms)}" for i, (key, ms) in enumerate(by_artist.most_common(10), 1)]
    lines += ["", "<b>Tracks</b>"]
    top_tracks = sorted(result.items, key=lambda t: -t.ms)[:5]
    lines += [f"{i}. {esc(t.track)} — {esc(t.artist)} · {hours(t.ms)}" for i, t in enumerate(top_tracks, 1)]
    if note := estimate_note(result):
        lines += ["", f"<i>{note}</i>"]
    await message.reply(truncate("\n".join(lines), TELEGRAM_TEXT_LIMIT))


@router.message(Command("timeleaderboard", "playtimeleaderboard", "listeningtimeleaderboard", "tlb", "ptlb"))
async def time_leaderboard(message: Message, command: CommandObject, db: Database, metadata: Metadata,
                           settings: Settings) -> None:
    """Who here listened the longest (port of the time leaderboard), from imported histories."""
    require_group(message)
    period, _ = parse_period((command.args or "").split(), default=WEEKLY)
    people = {m.id: m for m in await db.chat_lastfm_users(message.chat.id, settings.whoknows_max_members)
              if m.indexed_at}
    if not people:
        raise UserFacingError("The time leaderboard uses imported histories, and nobody here has one yet.")
    from_ts, to_ts = (0, int(time.time()) + 1) if period == ALL_TIME else period.bounds()
    rows = await db.track_playcounts(list(people), from_ts, to_ts)
    if not rows:
        raise UserFacingError(f"No plays here for {esc(period.label)}.")

    totals: Counter = Counter()
    names = {}
    for _, artist, track, plays in rows:
        key = (name_key(artist), name_key(track))
        totals[key] += plays
        names.setdefault(key, (artist, track))
    result = await listening_time(metadata, [(*names[k], p) for k, p in totals.items()])
    per_track = {(name_key(i.artist), name_key(i.track)): i.ms / i.plays for i in result.items}
    per_user: Counter = Counter()
    for user_id, artist, track, plays in rows:
        per_user[user_id] += per_track[(name_key(artist), name_key(track))] * plays

    lines = [f"<b>Listening time in {esc(message.chat.title or 'this group')}</b> · {esc(period.label)}", ""]
    for i, (user_id, ms) in enumerate(per_user.most_common(15), 1):
        user = people[user_id]
        name = esc(user.display_name or user.lastfm_username)
        if user.telegram_id == message.from_user.id:
            name = f"<b>{name}</b>"
        lines.append(f"{i}. {name} · {hours(int(ms))}")
    if note := estimate_note(result):
        lines += ["", f"<i>{note}</i>"]
    await message.reply(truncate("\n".join(lines), TELEGRAM_TEXT_LIMIT))


# --- lyrics ----------------------------------------------------------------------------------

LRCLIB = "https://lrclib.net/api"


async def find_lyrics(http: httpx.AsyncClient, artist: str, track: str) -> dict | None:
    """Exact match on LRCLIB first, then its search (first result with lyrics, by the same artist)."""
    response = await http.get(f"{LRCLIB}/get", params={"artist_name": artist, "track_name": track}, timeout=15)
    if response.status_code == 200:
        return response.json()
    if response.status_code != 404:
        response.raise_for_status()
    response = await http.get(f"{LRCLIB}/search", params={"artist_name": artist, "track_name": track}, timeout=15)
    response.raise_for_status()
    for found in response.json():
        if name_key(found.get("artistName")) == name_key(artist) and (found.get("plainLyrics") or found.get("instrumental")):
            return found
    return None


@router.message(Command("lyrics", "lyric", "lyr", "lr"))
async def lyrics(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database) -> None:
    target, rest = await resolve_target(message, (command.args or "").split(), db, search=True)
    artist, track = await resolve_subject("track", " ".join(rest), target, lastfm)
    try:
        found = await find_lyrics(lastfm.http, artist, track)
    except httpx.HTTPError:
        raise UserFacingError("The lyrics service isn't answering right now. Try again later.") from None
    if found is None:
        raise UserFacingError(f"No lyrics found for <b>{esc(track)}</b> by {esc(artist)}.")
    header = f"<b>{esc(found.get('trackName') or track)}</b> — {esc(found.get('artistName') or artist)}"
    if found.get("instrumental") and not found.get("plainLyrics"):
        await message.reply(f"{header}\n\nThis track is instrumental.")
        return
    footer = "\n<i>Lyrics from LRCLIB</i>"
    room = TELEGRAM_TEXT_LIMIT - len(header) - len(footer) - 80
    text = (found.get("plainLyrics") or "").strip()
    if len(esc(text)) > room:
        text = text[: room - 200].rsplit("\n", 1)[0] + "\n…"
    await message.reply(f"{header}\n<blockquote expandable>{esc(text)}</blockquote>{footer}")


# --- links -----------------------------------------------------------------------------------

async def _links(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database, links: MusicLinks, only: str | None):
    target, rest = await resolve_target(message, (command.args or "").split(), db, search=True)
    artist, track = await resolve_subject("track", " ".join(rest), target, lastfm)
    found = await links.for_track(artist, track)
    title = f"<b>{esc(track)}</b> — {esc(artist)}"

    if only == "spotify":
        label = "Open in Spotify" if found.spotify_exact else "Search on Spotify"
        await message.reply(f"{title}\n{link(label, found.spotify)}",
                            link_preview_options=LinkPreviewOptions(is_disabled=not found.spotify_exact,
                                                                   url=found.spotify))
        return
    if only == "youtube":
        label = "Watch on YouTube" if found.youtube_exact else "Search on YouTube"
        await message.reply(f"{title}\n{link(label, found.youtube)}",
                            link_preview_options=LinkPreviewOptions(is_disabled=not found.youtube_exact,
                                                                   url=found.youtube))
        return
    if only == "deezer":
        if not found.deezer:
            raise UserFacingError(f"Couldn't find {title} on Deezer.")
        await message.reply(f"{title}\n{link('Open in Deezer', found.deezer)}",
                            link_preview_options=LinkPreviewOptions(is_disabled=False, url=found.deezer))
        return

    parts = [link("Spotify" if found.spotify_exact else "Spotify (search)", found.spotify),
             link("YouTube" if found.youtube_exact else "YouTube (search)", found.youtube)]
    if found.deezer:
        parts.append(link("Deezer", found.deezer))
    if found.preview:
        parts.append(link("30s preview", found.preview))
    await message.reply(f"{title}\n" + " · ".join(parts))


@router.message(Command("spotify", "sp", "spotifyfind", "spotifysearch"))
async def spotify(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database, links: MusicLinks) -> None:
    await _links(message, command, lastfm, db, links, "spotify")


@router.message(Command("youtube", "yt", "youtubesearch", "ytsearch"))
async def youtube(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database, links: MusicLinks) -> None:
    await _links(message, command, lastfm, db, links, "youtube")


@router.message(Command("deezer", "dz"))
async def deezer_link(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database, links: MusicLinks) -> None:
    await _links(message, command, lastfm, db, links, "deezer")


@router.message(Command("links", "link", "listen"))
async def all_links(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database, links: MusicLinks) -> None:
    await _links(message, command, lastfm, db, links, None)
