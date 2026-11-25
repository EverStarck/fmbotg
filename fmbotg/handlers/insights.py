"""Index-based insights: /year, /discoveries, /artistpace, and /taste (ports of PlayBuilders.YearAsync,
ArtistBuilders discoveries/artistpace and UserBuilder taste)."""

import asyncio
from datetime import datetime

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from ..members import Members
from ..context import chat_title, resolve_subject, resolve_target, self_user
from ..domain import Target, UserFacingError
from ..db import Database
from ..formatting import TELEGRAM_TEXT_LIMIT, esc, lastfm_user_url, link, num, plural, span, truncate, user_link
from ..lastfm import LastFmClient
from ..periods import ALL_TIME, ALL_TIME_END, MONTHLY, parse_period, year_period
from ..playlogic import goal_amount, goal_date, taste
from ..stats import Stats, music_url

router = Router(name="insights")
TOP_N = 5


def _top_block(title: str, rows, kind: str, show_artist: bool = True) -> list[str]:
    if not rows:
        return []
    lines = ["", f"<b>{title}</b>"]
    for i, row in enumerate(rows[:TOP_N], 1):
        name = link(row.name, music_url(row.artist or row.name, row.name, kind))
        by = f" — {esc(row.artist)}" if row.artist and show_artist else ""
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
    from_ts, to_ts = period.bounds(tz=target.tz)
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
    target, rest = await resolve_target(message, args, db, search=True)
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
    reached = goal_date(now, goal - total, per_day)
    if reached is None:
        raise UserFacingError(f"At {per_day:,.2f} {esc(artist)} plays a day, <b>{num(goal)}</b> is more than "
                              "200 years away.")
    suffix = "" if period == ALL_TIME else f" ({esc(period.label)})"
    await message.reply(
        f"At this pace, {user_link(target.lastfm_username)} will reach <b>{num(goal)}</b> plays on "
        f"{link(artist, music_url(artist))} on <b>{reached:%d %B %Y}</b>.\n"
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
    caller = await self_user(message, db)
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



GAP_KINDS = {"artists": 3, "albums": 2, "tracks": 1}  # minimum plays, as in FMBot
GAP_DAYS = 90


@router.message(Command("gaps", "gap", "comebacks"))
async def gaps(message: Message, command: CommandObject, db: Database, stats: Stats) -> None:
    """Things you came back to after 90+ days away (port of PlayBuilders.ListeningGapsAsync)."""
    args = (command.args or "").split()
    kind = "artists"
    for arg in list(args):
        word = arg.lower().rstrip("s") + "s"
        if word in GAP_KINDS:
            kind = word
            args.remove(arg)
    target, _ = await resolve_target(message, args, db)
    await stats.require_index(target)
    rows = await db.listening_gaps(target.user_id, kind, GAP_KINDS[kind], GAP_DAYS * 86400)
    if not rows:
        raise UserFacingError(f"No {kind} that {user_link(target.lastfm_username)} came back to after "
                              f"{GAP_DAYS}+ days away.")
    lines = [f"<b>Comebacks for {user_link(target.lastfm_username)}</b> · {kind}", ""]
    for i, (name, artist, resumed, gap, total) in enumerate(rows[:20], 1):
        title = esc(name) if kind == "artists" else f"{esc(name)} — {esc(artist)}"
        when = datetime.fromtimestamp(resumed, target.tz)
        lines.append(f"{i}. {title} · <b>{span(gap / 86400)}</b> away, back on {when:%d %b %Y} "
                     f"<i>({plural(total, 'play')})</i>")
    lines += ["", "<i>Add albums or tracks to switch views.</i>"]
    await message.reply(truncate("\n".join(lines), TELEGRAM_TEXT_LIMIT))


@router.message(Command("search", "find"))
async def search(message: Message, command: CommandObject, db: Database, stats: Stats) -> None:
    """Search your own listening history (port of PlayBuilders.SearchAsync)."""
    caller = await self_user(message, db)
    target = Target.from_user(caller, True)
    query = (command.args or "").strip()
    if len(query) < 2:
        raise UserFacingError("Search for at least 2 characters, e.g. <code>/search love</code>.")
    await stats.require_index(target)

    lines = [f"<b>Search results for “{esc(query)}”</b>"]
    found = False
    for kind, title in (("artists", "Artists"), ("albums", "Albums"), ("tracks", "Tracks")):
        rows = await db.search_library(target.user_id, kind, query, limit=8)
        if not rows:
            continue
        found = True
        lines += ["", f"<b>{title}</b>"]
        for row in rows:
            name = link(row.name, music_url(row.artist or row.name, row.name, kind))
            by = f" — {esc(row.artist)}" if row.artist else ""
            lines.append(f"{name}{by} · {plural(row.playcount, 'play')}")
    if not found:
        raise UserFacingError(f"Nothing in your history matches “{esc(query)}”.")
    await message.reply(truncate("\n".join(lines), TELEGRAM_TEXT_LIMIT))


@router.message(Command("artistoverview", "ao", "aov"))
async def artist_overview(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database,
                          stats: Stats) -> None:
    """Everything about you and one artist (port of ArtistBuilders.ArtistOverviewAsync)."""
    target, rest = await resolve_target(message, (command.args or "").split(), db, search=True)
    subject = await resolve_subject("artist", " ".join(rest), target, lastfm)
    await stats.require_index(target)
    (artist,) = await stats.canonical_subject("artist", subject)
    total, first, last = await db.subject_stats(target.user_id, "artist", (artist,))
    if not total:
        raise UserFacingError(f"{user_link(target.lastfm_username)} has never played {esc(artist)}.")

    tracks = await db.top_from_plays(target.user_id, "tracks", 0, ALL_TIME_END, limit=TOP_N, artist=artist)
    albums = await db.top_from_plays(target.user_id, "albums", 0, ALL_TIME_END, limit=TOP_N, artist=artist)
    per_year: dict[int, int] = {}
    for ts in await db.subject_timestamps(target.user_id, "artist", (artist,)):
        year_ = datetime.fromtimestamp(ts, target.tz).year
        per_year[year_] = per_year.get(year_, 0) + 1

    first_day, last_day = (datetime.fromtimestamp(t, target.tz) for t in (first, last))
    lines = [f"<b>{link(artist, music_url(artist))}</b> for {user_link(target.lastfm_username)}", "",
             f"Plays: <b>{num(total)}</b>",
             f"Discovered: {first_day:%d %B %Y} · last played {last_day:%d %B %Y}"]
    lines += _top_block("Top tracks", tracks, "tracks", show_artist=False)
    lines += _top_block("Top albums", albums, "albums", show_artist=False)
    if per_year:
        peak = max(per_year.values())
        lines += ["", "<b>Plays per year</b>"]
        for y in sorted(per_year):
            bar = "█" * max(1, round(per_year[y] / peak * 12))
            lines.append(f"<code>{y} {bar}</code> {num(per_year[y])}")
    await message.reply(truncate("\n".join(lines), TELEGRAM_TEXT_LIMIT))


AFFINITY_TOP = 500


@router.message(Command("affinity", "aff", "neighbors"))
async def affinity(message: Message, db: Database, stats: Stats, members: Members) -> None:
    """Who in this group has the most similar taste to you (port of ArtistBuilders.AffinityAsync)."""
    if message.chat.type == "private":
        raise UserFacingError("Affinity compares you with the members of a group. Use it in a group chat.")
    caller = await self_user(message, db)
    others_here = [m for m in await members.linked(message.chat.id) if m.id != caller.id]
    if not others_here:
        raise UserFacingError("Nobody else in this chat has connected Last.fm yet.")

    own = await stats.all_time_artists(caller, AFFINITY_TOP)
    if not own:
        raise UserFacingError("Couldn't load your top artists from Last.fm.")
    others = await asyncio.gather(*(stats.all_time_artists(m, AFFINITY_TOP) for m in others_here))
    scores = sorted(((taste(own, theirs), m) for m, theirs in zip(others_here, others) if theirs),
                    key=lambda pair: -pair[0].similarity)

    lines = [f"<b>Taste neighbours of {esc(caller.shown_name)}</b> in "
             f"{esc(chat_title(message))}", ""]
    for i, (match, member) in enumerate(scores[:15], 1):
        shared = ", ".join(esc(name) for name, _, _ in match.shared[:3])
        lines.append(f"{i}. {link(member.shown_name, lastfm_user_url(member.lastfm_username))}"
                     f" · <b>{match.similarity}%</b>" + (f" <i>({shared})</i>" if shared else ""))
    lines += ["", f"<i>Based on all-time top {AFFINITY_TOP} artists.</i>"]
    await message.reply(truncate("\n".join(lines), TELEGRAM_TEXT_LIMIT))
