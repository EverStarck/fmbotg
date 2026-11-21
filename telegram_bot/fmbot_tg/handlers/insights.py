"""Index-based insights: /year, /discoveries, /artistpace, and /taste (ports of PlayBuilders.YearAsync,
ArtistBuilders discoveries/artistpace and UserBuilder taste)."""

from datetime import datetime, timedelta

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from ..context import NOT_REGISTERED_SELF, NotRegistered, Target, UserFacingError, resolve_subject, resolve_target
from ..db import Database
from ..formatting import TELEGRAM_TEXT_LIMIT, span, esc, link, num, plural, truncate, user_link
from ..lastfm import LastFmClient
from ..periods import ALL_TIME, MONTHLY, parse_period, year_period
from ..playlogic import goal_amount, taste
from ..stats import Stats, music_url

router = Router(name="insights")
TOP_N = 5


def _top_block(title: str, rows, kind: str) -> list[str]:
    if not rows:
        return []
    lines = ["", f"<b>{title}</b>"]
    for i, row in enumerate(rows[:TOP_N], 1):
        name = link(row.name, music_url(row.artist or row.name, row.name, kind))
        by = f" — {esc(row.artist)}" if row.artist else ""
        lines.append(f"{i}. {name}{by} · {num(row.playcount)}")
    return lines


@router.message(Command("year", "yr", "wrapped"))
async def year(message: Message, command: CommandObject, db: Database, stats: Stats) -> None:
    """Year in review from the play index (port of PlayBuilders.YearAsync)."""
    args = (command.args or "").split()
    target, rest = await resolve_target(message, args, db)
    period, rest = parse_period(rest, default=year_period(datetime.now(target.tz).year))
    if period.year is None:
        raise UserFacingError("Give a year, e.g. <code>/year 2023</code>.")
    await stats.require_index(target)

    from_ts, to_ts = period.bounds(tz=target.tz)
    total = await db.count_plays(target.user_id, from_ts, to_ts)
    if not total:
        raise UserFacingError(f"{user_link(target.lastfm_username)} has no scrobbles in {period.year}.")
    artists = await db.top_from_plays(target.user_id, "artists", from_ts, to_ts, limit=100_000)
    albums = await db.top_from_plays(target.user_id, "albums", from_ts, to_ts, limit=TOP_N)
    tracks = await db.top_from_plays(target.user_id, "tracks", from_ts, to_ts, limit=TOP_N)
    new = await db.discoveries(target.user_id, from_ts, to_ts, limit=100_000)

    days = max(1, (min(to_ts, int(datetime.now(target.tz).timestamp())) - from_ts) // 86400)
    lines = [
        f"<b>{period.year} for {user_link(target.lastfm_username)}</b>",
        "",
        f"Scrobbles: <b>{num(total)}</b> ({total / days:,.1f} per day)",
        f"Artists: <b>{num(len(artists))}</b>, of which <b>{num(len(new))}</b> new",
    ]
    lines += _top_block("Top artists", artists, "artists")
    lines += _top_block("Top albums", albums, "albums")
    lines += _top_block("Top tracks", tracks, "tracks")
    if new:
        lines += ["", "<b>Top new artists</b>"]
        lines += [f"{i}. {link(name, music_url(name))} · {num(count)}" for i, (name, _, count) in enumerate(new[:TOP_N], 1)]
    await message.reply(truncate("\n".join(lines), TELEGRAM_TEXT_LIMIT))


@router.message(Command("discoveries", "discovered", "ds"))
async def discoveries(message: Message, command: CommandObject, db: Database, stats: Stats) -> None:
    """Artists you first listened to in a period (port of ArtistBuilders discoveries)."""
    period, args = parse_period((command.args or "").split(), default=MONTHLY)
    target, _ = await resolve_target(message, args, db)
    await stats.require_index(target)
    from_ts, to_ts = period.bounds(tz=target.tz) if period != ALL_TIME else (0, 2**62)
    rows = await db.discoveries(target.user_id, from_ts, to_ts, limit=20)
    if not rows:
        raise UserFacingError(f"No new artists for {user_link(target.lastfm_username)} ({esc(period.label)}).")

    lines = [f"<b>Artists discovered by {user_link(target.lastfm_username)}</b> · {esc(period.label)}", ""]
    for i, (artist, first, count) in enumerate(rows, 1):
        day = datetime.fromtimestamp(first, target.tz)
        lines.append(f"{i}. {link(artist, music_url(artist))} · {plural(count, 'play')} · <i>since {day:%d %b %Y}</i>")
    await message.reply(truncate("\n".join(lines), TELEGRAM_TEXT_LIMIT))


@router.message(Command("artistpace", "apc"))
async def artist_pace(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database,
                      stats: Stats) -> None:
    """When will you reach N plays on an artist (port of ArtistBuilders.ArtistPaceAsync)."""
    period, args = parse_period((command.args or "").split(), default=ALL_TIME, edges_only=True)
    target, rest = await resolve_target(message, args, db)
    await stats.require_index(target)

    (artist,) = await stats.canonical_subject("artist", await _artist_for_pace(rest, target, lastfm))
    total, first, _ = await db.subject_stats(target.user_id, "artist", (artist,))
    if not total:
        raise UserFacingError(f"{user_link(target.lastfm_username)} has never played {esc(artist)}.")
    goal, _ = goal_amount([a for a in rest if _is_amount(a)], total)

    now = datetime.now(target.tz)
    if period == ALL_TIME:
        start, count = first, total
    else:
        start, end = period.bounds(tz=target.tz)
        count, _, _ = await db.subject_stats(target.user_id, "artist", (artist,), start, end)
    if not count:
        raise UserFacingError(f"No plays on {esc(artist)} in this period ({esc(period.label)}).")
    days = max((now.timestamp() - start) / 86400, 1 / 24)
    per_day = count / days
    goal_date = now + timedelta(days=(goal - total) / per_day)
    suffix = "" if period == ALL_TIME else f" ({esc(period.label)})"
    await message.reply(
        f"At this pace, {user_link(target.lastfm_username)} will reach <b>{num(goal)}</b> plays on "
        f"{link(artist, music_url(artist))} on <b>{goal_date:%d %B %Y}</b>.\n"
        f"<i>Currently {num(total)} plays; {per_day:,.2f} per day over {span(days)}{suffix}.</i>"
    )


def _is_amount(word: str) -> bool:
    word = word.lower().replace(",", "")
    return word.isdigit() or (word.endswith("k") and word[:-1].isdigit())


async def _artist_for_pace(rest: list[str], target: Target, lastfm: LastFmClient) -> tuple[str, ...]:
    # "/artistpace 500 Radiohead": the goal is a number, the rest is the artist
    query = " ".join(word for word in rest if not _is_amount(word))
    return await resolve_subject("artist", query, target, lastfm)


@router.message(Command("taste", "t", "compare"))
async def taste_cmd(message: Message, command: CommandObject, db: Database, stats: Stats) -> None:
    """Compare your top artists with someone else's (port of UserBuilder.TasteAsync)."""
    caller = await db.get_user(message.from_user.id)
    if caller is None or not caller.lastfm_username:
        raise NotRegistered(NOT_REGISTERED_SELF)
    period, args = parse_period((command.args or "").split(), default=ALL_TIME)
    other, _ = await resolve_target(message, args, db)
    if other.is_self:
        raise UserFacingError("Reply to someone's message, or add <code>@handle</code> or "
                              "<code>lfm:username</code> to compare with them.")
    me = Target.from_user(caller, True)

    async def top_artists(target: Target) -> list[tuple[str, int]]:
        result = await stats.top(target, "artists", period, page=1, page_size=1000)
        return [(item.name, item.playcount) for item in result.items]

    match = taste(await top_artists(me), await top_artists(other))
    you, them = user_link(me.lastfm_username), user_link(other.lastfm_username)
    lines = [f"<b>Taste: {you} vs {them}</b> · {esc(period.label)}", "",
             f"Match: <b>{match.similarity}%</b> · {plural(len(match.shared), 'shared artist')}"]
    if match.shared:
        lines += ["", f"<i>artist — {esc(me.lastfm_username)} · {esc(other.lastfm_username)}</i>"]
        lines += [f"{link(name, music_url(name))} — {num(a)} · {num(b)}" for name, a, b in match.shared[:15]]
    await message.reply(truncate("\n".join(lines), TELEGRAM_TEXT_LIMIT))

