"""Genres and countries: /topgenres, /genre, /whoknowsgenre, /fwkgenre and the same for countries
(port of GenreBuilders, CountryBuilders and their who knows commands). Each command is written
once and works for both through a `Dimension`."""

import asyncio

from aiogram import Router
from aiogram.filters import CommandObject
from aiogram.types import Message

from ..boards import Entry, render_board
from ..context import chat_title, require_group, resolve_subject, resolve_target, self_user
from ..db import Database, User
from ..dimensions import DIMENSIONS, Category, Dimension, artist_rows, category_lines, pending_note
from ..domain import Target, UserFacingError
from ..formatting import TELEGRAM_TEXT_LIMIT, esc, plural, truncate, user_link
from ..lastfm import LastFmClient
from ..members import Members
from ..metadata import Metadata
from ..names import name_key
from ..periods import ALL_TIME, parse_period
from ..routing import add_kind_commands
from ..social import friend_roster
from ..stats import Stats

router = Router(name="genres")

MEMBER_ARTISTS = 200  # per person in who knows genre/country
SHOWN = 15


async def _artist_categories(dimension: Dimension, artist_query: str, target: Target | None, lastfm: LastFmClient,
                             stats: Stats, metadata: Metadata) -> tuple[str, list[Category], bool]:
    """(artist, its categories, still pending) for a searched artist, or what the target is playing."""
    artist = (await stats.canonical_subject("artist", await resolve_subject("artist", artist_query, target, lastfm)))[0]
    lookup = await dimension.lookup(metadata, [artist], wait=dimension.single_wait)
    return artist, dimension.categories(lookup.values.get(name_key(artist))), bool(lookup.pending)


# --- top genres / countries -------------------------------------------------------------------

async def _top(message: Message, kind: str, command: CommandObject, db: Database, stats: Stats,
               metadata: Metadata) -> None:
    dimension = DIMENSIONS[kind]
    period, args = parse_period((command.args or "").split())
    target, _ = await resolve_target(message, args, db)
    rows = await artist_rows(stats, target, period, dimension.top_artists)
    lookup = await dimension.lookup(metadata, [a for a, _ in rows])
    groups = dimension.aggregate(rows, lookup.values)
    if not groups:
        raise UserFacingError(f"No {dimension.plural} known yet for {user_link(target.lastfm_username)}'s artists "
                              f"({esc(period.label)})." + (" They're still being looked up; try again in a minute."
                                                           if lookup.pending else ""))
    lines = [f"<b>Top {dimension.plural} for {user_link(target.lastfm_username)}</b> · {esc(period.label)}", ""]
    lines += category_lines(dimension, groups, SHOWN)
    lines += ["", f"<code>From the top {plural(len(rows), 'artist')}</code>"]
    if note := pending_note(lookup, dimension):
        lines += ["", note]
    await message.reply(truncate("\n".join(lines), TELEGRAM_TEXT_LIMIT))


add_kind_commands(router, _top, {
    "genre": ("topgenres", "tg", "genres"),
    "country": ("topcountries", "countries", "countrychart", "tc"),
})


# --- one genre / country ----------------------------------------------------------------------

async def _one(message: Message, kind: str, command: CommandObject, lastfm: LastFmClient, db: Database,
               stats: Stats, metadata: Metadata) -> None:
    """Your top artists in a genre or from a country; given an artist (or nothing: what you're playing),
    its genres or country, then your top artists in the first one."""
    dimension = DIMENSIONS[kind]
    period, args = parse_period((command.args or "").split(), default=ALL_TIME)
    target, rest = await resolve_target(message, args, db, search=True)
    query = " ".join(rest)
    rows = await artist_rows(stats, target, period, dimension.top_artists)
    lookup = await dimension.lookup(metadata, [a for a, _ in rows])
    known = [category for category, _, _ in dimension.aggregate(rows, lookup.values)]

    lines = []
    category = dimension.find(query, known) if query else None
    if category is None:
        artist, categories, pending = await _artist_categories(dimension, query, target, lastfm, stats, metadata)
        if not categories:
            if pending:
                raise UserFacingError(f"Still looking up <b>{esc(artist)}</b>; try again in a minute.")
            if query:
                raise UserFacingError(f"<b>{esc(query)}</b> isn't a {dimension.name} in your top artists, and no "
                                      f"{dimension.name} is known for an artist by that name.")
            raise UserFacingError(f"No {dimension.name} known for <b>{esc(artist)}</b>.")
        category = categories[0]
        lines += [dimension.describe(artist, categories), ""]

    whose = "Your" if target.is_self else f"{user_link(target.lastfm_username)}'s"
    matching = [(a, p) for a, p in rows
                if any(c.key == category.key for c in dimension.categories(lookup.values.get(name_key(a))))]
    lines += [f"{dimension.list_title(whose, category)} · {esc(period.label)}", ""]
    lines += [f"{i}. {esc(artist)} · {plural(plays, 'play')}" for i, (artist, plays) in enumerate(matching[:SHOWN], 1)]
    if len(matching) > SHOWN:
        lines.append(f"<i>…and {plural(len(matching) - SHOWN, 'more artist')}</i>")
    if matching:
        lines += ["", f"<code>{plural(sum(p for _, p in matching), 'play')} from {plural(len(matching), 'artist')}</code>"]
    else:
        lines.append("None in the top artists for this period.")
    if note := pending_note(lookup, dimension):
        lines += ["", note]
    await message.reply(truncate("\n".join(lines), TELEGRAM_TEXT_LIMIT))


add_kind_commands(router, _one, {"genre": ("genre", "g"), "country": ("country", "from")})


# --- who knows a genre / country --------------------------------------------------------------

async def _whoknows(message: Message, command: CommandObject, dimension: Dimension, people: list[User], scope: str,
                    lastfm: LastFmClient, db: Database, stats: Stats, metadata: Metadata) -> None:
    query = " ".join((command.args or "").split())
    per_person = list(zip(people, await asyncio.gather(*(stats.all_time_artists(p, MEMBER_ARTISTS) for p in people))))

    # Look up the most played artists across everyone first
    totals: dict[str, tuple[str, int]] = {}
    for _, rows in per_person:
        for artist, plays in rows or []:
            seen = totals.get(name_key(artist))
            totals[name_key(artist)] = (artist, plays + (seen[1] if seen else 0))
    ranked = sorted(totals.values(), key=lambda pair: -pair[1])
    lookup = await dimension.lookup(metadata, [a for a, _ in ranked])

    if query:
        known = [category for category, _, _ in dimension.aggregate(ranked, lookup.values)]
        category = dimension.find(query, known) or dimension.guess(query)
        if category is None:
            raise UserFacingError(f"Nobody's top artists are from <b>{esc(query)}</b> yet"
                                  + (", but some are still being looked up." if lookup.pending else "."))
    else:
        target, _ = await resolve_target(message, [], db)
        artist, categories, _ = await _artist_categories(dimension, "", target, lastfm, stats, metadata)
        if not categories:
            raise UserFacingError(f"No {dimension.name} known for <b>{esc(artist)}</b>. Add one, e.g. "
                                  f"<code>/whoknows{dimension.name} {dimension.example}</code>.")
        category = categories[0]

    def counts(value) -> bool:
        return any(c.key == category.key for c in dimension.categories(value))

    entries, failed = [], 0
    for person, rows in per_person:
        if rows is None:
            failed += 1
            continue
        plays = sum(p for a, p in rows if counts(lookup.values.get(name_key(a))))
        if plays:
            entries.append(Entry(person.shown_name, person.lastfm_username, plays,
                                 person.telegram_id == message.from_user.id))
    if not entries:
        raise UserFacingError(f"Nobody {scope} listens to <b>{esc(category.title)}</b> artists"
                              + (" yet; some artists are still being looked up." if lookup.pending else "."))
    entries.sort(key=lambda e: -e.plays)
    note = pending_note(lookup, dimension)
    heading = f"<b>Who knows {esc(category.title)}</b> {scope}"
    await message.reply(truncate(render_board(heading, entries, failed, [note] if note else []), TELEGRAM_TEXT_LIMIT))


async def _group(message: Message, kind: str, command: CommandObject, lastfm: LastFmClient, db: Database,
                 stats: Stats, metadata: Metadata, members: Members) -> None:
    require_group(message, "Who knows")
    people = await members.require_linked(message.chat.id)
    scope = f"in {esc(chat_title(message))}"
    await _whoknows(message, command, DIMENSIONS[kind], people, scope, lastfm, db, stats, metadata)


async def _friends(message: Message, kind: str, command: CommandObject, lastfm: LastFmClient, db: Database,
                   stats: Stats, metadata: Metadata) -> None:
    people = await friend_roster(await self_user(message, db), db, lastfm)
    await _whoknows(message, command, DIMENSIONS[kind], people, "among your friends", lastfm, db, stats, metadata)


add_kind_commands(router, _group, {"genre": ("whoknowsgenre", "wkg"), "country": ("whoknowscountry", "wkc")})
add_kind_commands(router, _friends, {
    "genre": ("friendwhoknowsgenre", "fwkgenre", "fwkg"),
    "country": ("friendwhoknowscountry", "fwkcountry", "fwkc"),
})
