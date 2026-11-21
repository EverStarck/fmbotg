"""Play statistics commands (port of PlaySlashCommands / PlayBuilders and the *plays commands of
ArtistSlashCommands, AlbumSlashCommands and TrackSlashCommands)."""

import time
from datetime import datetime, timedelta

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from ..context import Target, UserFacingError, resolve_subject, resolve_target
from ..db import Database, SavedStreak
from ..formatting import TELEGRAM_TEXT_LIMIT, span, ago, esc, link, num, ordinal, plural, truncate, user_link
from ..lastfm import LastFmClient
from ..periods import ALL_TIME, Period, parse_period
from ..playlogic import goal_amount, milestone_amount, overview
from ..stats import Stats, music_url

router = Router(name="plays")


def _when(timestamp: int | None, target: Target) -> str:
    if not timestamp:
        return "unknown"
    moment = datetime.fromtimestamp(timestamp, target.tz)
    return f"{moment:%d %B %Y}, {moment:%H:%M} ({ago(timestamp)})"


def _period_suffix(period: Period) -> str:
    return "" if period == ALL_TIME else f" ({esc(period.label)})"


@router.message(Command("plays", "p", "scrobbles"))
async def plays(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database) -> None:
    period, args = parse_period((command.args or "").split(), default=ALL_TIME)
    target, _ = await resolve_target(message, args, db)
    if period == ALL_TIME:
        count = int((await lastfm.user_info(target.lastfm_username)).get("playcount") or 0)
    else:
        from_ts, to_ts = period.bounds(tz=target.tz)
        count = await lastfm.scrobble_count(target.lastfm_username, from_ts, to_ts, target.session_key)
    await message.reply(f"{user_link(target.lastfm_username)} has <b>{plural(count, 'scrobble')}</b>"
                        f"{_period_suffix(period)}.")


async def _subject_plays(message: Message, command: CommandObject, kind: str, lastfm: LastFmClient,
                         db: Database, stats: Stats) -> None:
    period, args = parse_period((command.args or "").split(), default=ALL_TIME, edges_only=True)
    target, rest = await resolve_target(message, args, db)
    subject = await resolve_subject(kind, " ".join(rest), target, lastfm)

    if period != ALL_TIME:
        await stats.require_index(target)
    subject = await stats.canonical_subject(kind, subject)
    if period == ALL_TIME:
        count = await lastfm.user_playcount(kind, subject, target.lastfm_username)
    else:
        from_ts, to_ts = period.bounds(tz=target.tz)
        count, _, _ = await db.subject_stats(target.user_id, kind, subject, from_ts, to_ts)
    name = " - ".join(subject)

    url = music_url(subject[0], subject[1] if len(subject) > 1 else None, kind + "s")
    await message.reply(f"{user_link(target.lastfm_username)} has <b>{plural(count, 'play')}</b> on "
                        f"{link(name, url)}{_period_suffix(period)}.")


@router.message(Command("artistplays", "ap"))
async def artist_plays(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database,
                       stats: Stats) -> None:
    await _subject_plays(message, command, "artist", lastfm, db, stats)


@router.message(Command("albumplays", "abp"))
async def album_plays(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database,
                      stats: Stats) -> None:
    await _subject_plays(message, command, "album", lastfm, db, stats)


@router.message(Command("trackplays", "tp"))
async def track_plays(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database,
                      stats: Stats) -> None:
    await _subject_plays(message, command, "track", lastfm, db, stats)


@router.message(Command("streak", "str", "combo"))
async def streak(message: Message, command: CommandObject, db: Database, stats: Stats) -> None:
    target, _ = await resolve_target(message, (command.args or "").split(), db)
    result = await stats.streak(target)
    if result is None:
        raise UserFacingError(f"{user_link(target.lastfm_username)} has no scrobbles yet.")

    cur = result.current
    lines = [f"<b>Streak for {user_link(target.lastfm_username)}</b>", ""]
    if result.artist_count > 1:
        lines.append(f"Artist: <b>{plural(result.artist_count, 'play')}</b> in a row — {esc(cur.artist)}")
    if result.album_count > 1:
        lines.append(f"Album: <b>{plural(result.album_count, 'play')}</b> in a row — {esc(cur.album)}")
    if result.track_count > 1:
        lines.append(f"Track: <b>{plural(result.track_count, 'play')}</b> in a row — {esc(cur.track)}")
    if len(lines) == 2:
        await message.reply(f"No active streak for {user_link(target.lastfm_username)}.")
        return
    if result.started:
        lines += ["", f"<i>Started {_when(result.started, target)}</i>"]
    if not target.indexed and result.alive:
        lines.append("<i>Only the last 1,000 scrobbles were checked; /update imports the full history.</i>")
    if result.worth_saving and result.started and target.user_id is not None:
        saved = SavedStreak(artist=cur.artist, album=cur.album, track=cur.track,
                            artist_count=result.artist_count, album_count=result.album_count,
                            track_count=result.track_count, started=result.started,
                            ended=cur.played_at or int(time.time()))
        if await db.save_streak(target.user_id, saved):
            lines.append("<i>Saved to your /streaks.</i>")
    await message.reply("\n".join(lines))


@router.message(Command("streaks", "streakhistory"))
async def streaks(message: Message, command: CommandObject, db: Database) -> None:
    """Saved streaks of 25+ plays (port of PlayBuilders.StreaksAsync)."""
    target, _ = await resolve_target(message, (command.args or "").split(), db)
    rows = await db.saved_streaks(target.user_id) if target.user_id is not None else []
    if not rows:
        raise UserFacingError(f"No saved streaks for {user_link(target.lastfm_username)} yet. "
                              "Streaks of 25 or more plays are saved when you run /streak.")
    lines = [f"<b>Saved streaks for {user_link(target.lastfm_username)}</b>", ""]
    for row in rows:
        start = datetime.fromtimestamp(row.started, target.tz)
        best = max((row.artist_count, row.artist), (row.album_count, row.album or ""), (row.track_count, row.track))
        lines.append(f"<b>{start:%d %b %Y}</b> · {esc(best[1])} · <b>{plural(best[0], 'play')}</b>"
                     f" <i>(artist {num(row.artist_count)}, album {num(row.album_count)}, "
                     f"track {num(row.track_count)})</i>")
    await message.reply(truncate("\n".join(lines), TELEGRAM_TEXT_LIMIT))


@router.message(Command("pace", "pc"))
async def pace(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database) -> None:
    """Port of PlayBuilders.PaceAsync: when will the user hit a scrobble goal at their current rate."""
    period, args = parse_period((command.args or "").split(), default=ALL_TIME)
    target, rest = await resolve_target(message, args, db)
    info = await lastfm.user_info(target.lastfm_username)
    total = int(info.get("playcount") or 0)
    goal, _ = goal_amount(rest, total)

    now = datetime.now(target.tz)
    if period == ALL_TIME:
        start = int((info.get("registered") or {}).get("unixtime") or 0)
        count = total
    else:
        start, _ = period.bounds(tz=target.tz)
        count = await lastfm.scrobble_count(target.lastfm_username, start, None, target.session_key)
    if not count or not start:
        raise UserFacingError(f"No scrobbles for {user_link(target.lastfm_username)}{_period_suffix(period)}.")

    days = max((now.timestamp() - start) / 86400, 1 / 24)
    per_day = count / days
    goal_date = now + timedelta(days=(goal - total) / per_day)
    await message.reply(
        f"At their current pace, {user_link(target.lastfm_username)} will reach "
        f"<b>{num(goal)}</b> scrobbles on <b>{goal_date:%d %B %Y}</b>.\n"
        f"<i>Based on {per_day:,.1f} scrobbles per day: {num(count)} scrobbles over "
        f"{span(days)}{_period_suffix(period)}.</i>"
    )


@router.message(Command("milestone", "ms"))
async def milestone(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database) -> None:
    target, args = await resolve_target(message, (command.args or "").split(), db)
    total = await lastfm.scrobble_count(target.lastfm_username, session_key=target.session_key)
    if total < 1:
        raise UserFacingError(f"{user_link(target.lastfm_username)} has no scrobbles yet.")
    number, is_random, _ = milestone_amount(args, total)
    play = await lastfm.scrobble_at(target.lastfm_username, number, total, target.session_key)
    if play is None:
        raise UserFacingError("Couldn't find that scrobble.")
    title = "Random scrobble" if is_random else f"{ordinal(number)} scrobble"
    await message.reply(
        f"<b>{title} for {user_link(target.lastfm_username)}</b> (#{num(number)})\n\n"
        f"<b>{link(play.track, play.url)}</b>\nby <b>{esc(play.artist)}</b>"
        + (f" · <i>{esc(play.album)}</i>" if play.album else "")
        + f"\n\n<i>{_when(play.timestamp, target)}</i>"
    )


@router.message(Command("overview", "ov"))
async def overview_cmd(message: Message, command: CommandObject, db: Database, stats: Stats) -> None:
    args = (command.args or "").split()
    days = 4
    for arg in list(args):
        if arg.isdigit():
            days = max(1, min(int(arg), 8))
            args.remove(arg)
            break
    target, _ = await resolve_target(message, args, db)

    now = datetime.now(target.tz)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
    plays_ = await stats.plays_window(target, int(start.timestamp()), int(now.timestamp()) + 1)
    if not plays_:
        raise UserFacingError(f"No scrobbles for {user_link(target.lastfm_username)} in the last "
                              f"{plural(days, 'day')}.")

    lines = [f"<b>Daily overview for {user_link(target.lastfm_username)}</b>"]
    for day in overview(plays_, target.tz):
        lines += ["", f"<b>{day.day:%A %d %B}</b> · {plural(day.plays, 'play')}"]
        if day.top_artist:
            lines.append(f"🎤 {esc(day.top_artist[0])} · {day.top_artist[1]}")
        if day.top_album:
            lines.append(f"💿 {esc(day.top_album[0])} — {esc(day.top_album[1])} · {day.top_album[2]}")
        if day.top_track:
            lines.append(f"🎶 {esc(day.top_track[0])} — {esc(day.top_track[1])} · {day.top_track[2]}")
    await message.reply(truncate("\n".join(lines), TELEGRAM_TEXT_LIMIT))


async def _artist_top(message: Message, command: CommandObject, kind: str, lastfm: LastFmClient, db: Database,
                      stats: Stats) -> None:
    period, args = parse_period((command.args or "").split(), default=ALL_TIME, edges_only=True)
    target, rest = await resolve_target(message, args, db)
    (artist,) = await resolve_subject("artist", " ".join(rest), target, lastfm)
    await stats.require_index(target)
    (artist,) = await stats.canonical_subject("artist", (artist,))

    from_ts, to_ts = period.bounds(tz=target.tz) if period != ALL_TIME else (0, 2**62)
    rows = await db.top_from_plays(target.user_id, kind, from_ts, to_ts, limit=25, artist=artist)
    if not rows:
        raise UserFacingError(f"{user_link(target.lastfm_username)} has no plays on {esc(artist)}"
                              f"{_period_suffix(period)}.")
    lines = [f"<b>{user_link(target.lastfm_username)}'s top {kind} by {esc(artist)}</b>{_period_suffix(period)}", ""]
    for i, row in enumerate(rows, 1):
        lines.append(f"{i}. {link(row.name, music_url(artist, row.name, kind))} · <b>{num(row.playcount)}</b>")
    await message.reply(truncate("\n".join(lines), TELEGRAM_TEXT_LIMIT))


@router.message(Command("artisttracks", "at"))
async def artist_tracks(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database,
                        stats: Stats) -> None:
    await _artist_top(message, command, "tracks", lastfm, db, stats)


@router.message(Command("artistalbums", "aa"))
async def artist_albums(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database,
                        stats: Stats) -> None:
    await _artist_top(message, command, "albums", lastfm, db, stats)


async def _discovery(message: Message, command: CommandObject, kind: str, lastfm: LastFmClient, db: Database,
                     stats: Stats) -> None:
    """Port of discoverydate + lastlistened: first and last time you played something."""
    target, rest = await resolve_target(message, (command.args or "").split(), db)
    subject = await resolve_subject(kind, " ".join(rest), target, lastfm)
    await stats.require_index(target)
    subject = await stats.canonical_subject(kind, subject)
    count, first, last = await db.subject_stats(target.user_id, kind, subject)
    name = " - ".join(subject)
    if not count:
        raise UserFacingError(f"{user_link(target.lastfm_username)} has never played {esc(name)}.")
    url = music_url(subject[0], subject[1] if len(subject) > 1 else None, kind + "s")
    await message.reply(
        f"<b>{link(name, url)}</b> for {user_link(target.lastfm_username)}\n\n"
        f"Discovered: {_when(first, target)}\n"
        f"Last played: {_when(last, target)}\n"
        f"Plays: <b>{num(count)}</b>"
    )


@router.message(Command("discovery", "discoverydate", "lastlistened", "firstlistened", "dd", "ll"))
async def discovery(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database,
                    stats: Stats) -> None:
    await _discovery(message, command, "artist", lastfm, db, stats)


@router.message(Command("albumdiscovery", "abdd", "albumlastlistened"))
async def album_discovery(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database,
                          stats: Stats) -> None:
    await _discovery(message, command, "album", lastfm, db, stats)


@router.message(Command("trackdiscovery", "tdd", "tracklastlistened"))
async def track_discovery(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database,
                          stats: Stats) -> None:
    await _discovery(message, command, "track", lastfm, db, stats)
