"""Genres and countries: /topgenres, /genre, /whoknowsgenre, /fwkgenre and the same for countries
(port of GenreBuilders, CountryBuilders and their who knows commands)."""

import asyncio

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from ..config import Settings
from ..context import Target, UserFacingError, require_group, resolve_subject, resolve_target, self_user
from ..db import Database, User, name_key
from ..formatting import TELEGRAM_TEXT_LIMIT, esc, plural, truncate, user_link
from ..lastfm import LastFmClient, LastFmError
from ..metadata import Lookup, Metadata, by_country, by_genre, flag, genre_key, match_country
from ..periods import ALL_TIME, Period, parse_period
from ..stats import Stats
from .friends import friend_names
from .whoknows import Entry, render_board

router = Router(name="genres")

TOP_ARTISTS = 200  # top artists a genre/country breakdown is based on
MEMBER_ARTISTS = 200  # per person in who knows genre/country
SHOWN = 15
LOOKUPS = 6


def _pending_note(lookup: Lookup, what: str) -> list[str]:
    if not lookup.pending:
        return []
    return ["", f"<i>Still looking up {what} for {plural(lookup.pending, 'artist')}; "
                "run it again in a minute for a fuller picture.</i>"]


async def artist_rows(stats: Stats, target: Target, period: Period, limit: int = TOP_ARTISTS) -> list[tuple[str, int]]:
    top = await stats.top(target, "artists", period, page=1, page_size=limit)
    return [(item.name, item.playcount) for item in top.items]


async def _countries(metadata: Metadata, artists: list[str]) -> Lookup:
    # MusicBrainz allows one lookup per second, so only queue the top artists
    return await metadata.countries(artists, max_new=100)


def _artist_list(artists: list[str], limit: int = 3) -> str:
    return ", ".join(esc(a) for a in artists[:limit])


# --- top genres / countries -------------------------------------------------------------------

@router.message(Command("topgenres", "tg", "genres"))
async def top_genres(message: Message, command: CommandObject, db: Database, stats: Stats,
                     metadata: Metadata) -> None:
    period, args = parse_period((command.args or "").split())
    target, _ = await resolve_target(message, args, db)
    rows = await artist_rows(stats, target, period)
    lookup = await metadata.genres([a for a, _ in rows])
    genres = by_genre(rows, lookup.values)
    if not genres:
        raise UserFacingError(f"No genres found for {user_link(target.lastfm_username)} ({esc(period.label)})."
                              + (" Genres are still being looked up; try again in a minute." if lookup.pending else ""))
    lines = [f"<b>Top genres for {user_link(target.lastfm_username)}</b> · {esc(period.label)}", ""]
    for i, (genre, plays, artists) in enumerate(genres[:SHOWN], 1):
        lines.append(f"{i}. <b>{esc(genre)}</b> · {plural(plays, 'play')} · <i>{_artist_list(artists)}</i>")
    lines += ["", f"<code>From the top {plural(len(rows), 'artist')}</code>"] + _pending_note(lookup, "genres")
    await message.reply(truncate("\n".join(lines), TELEGRAM_TEXT_LIMIT))


@router.message(Command("topcountries", "countries", "countrychart", "tc"))
async def top_countries(message: Message, command: CommandObject, db: Database, stats: Stats,
                        metadata: Metadata) -> None:
    period, args = parse_period((command.args or "").split())
    target, _ = await resolve_target(message, args, db)
    rows = await artist_rows(stats, target, period, limit=100)
    lookup = await _countries(metadata, [a for a, _ in rows])
    countries = by_country(rows, lookup.values)
    if not countries:
        raise UserFacingError(f"No countries known yet for {user_link(target.lastfm_username)}'s artists "
                              f"({esc(period.label)}). They're being looked up; try again in a minute.")
    total = sum(plays for _, _, plays, _ in countries)
    lines = [f"<b>Top countries for {user_link(target.lastfm_username)}</b> · {esc(period.label)}", ""]
    for i, (code, country, plays, artists) in enumerate(countries[:SHOWN], 1):
        share = f"{plays / total:.0%}" if total else ""
        lines.append(f"{i}. {flag(code)} <b>{esc(country)}</b> · {plural(plays, 'play')} ({share}) · "
                     f"<i>{_artist_list(artists)}</i>")
    lines += ["", f"<code>From the top {plural(len(rows), 'artist')}</code>"] + _pending_note(lookup, "countries")
    await message.reply(truncate("\n".join(lines), TELEGRAM_TEXT_LIMIT))


# --- one genre / country ----------------------------------------------------------------------

def _artists_in(rows: list[tuple[str, int]], values: dict, match) -> list[tuple[str, int]]:
    return [(artist, plays) for artist, plays in rows if match(values.get(name_key(artist)))]


def _ranked(title: str, matching: list[tuple[str, int]]) -> list[str]:
    lines = [title]
    for i, (artist, plays) in enumerate(matching[:SHOWN], 1):
        lines.append(f"{i}. {esc(artist)} · {plural(plays, 'play')}")
    if len(matching) > SHOWN:
        lines.append(f"<i>…and {plural(len(matching) - SHOWN, 'more artist')}</i>")
    return lines


@router.message(Command("genre", "g"))
async def genre(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database, stats: Stats,
                metadata: Metadata) -> None:
    """/genre: your top artists in a genre, or an artist's genres (defaults to what you're playing)."""
    period, args = parse_period((command.args or "").split(), default=ALL_TIME)
    target, rest = await resolve_target(message, args, db, search=True)
    query = " ".join(rest)
    rows = await artist_rows(stats, target, period)
    lookup = await metadata.genres([a for a, _ in rows])
    whose = "Your" if target.is_self else f"{user_link(target.lastfm_username)}'s"

    wanted = genre_key(query) if query else None
    if wanted and any(wanted == genre_key(g) for g, _, _ in by_genre(rows, lookup.values)):
        name = next(g for g, _, _ in by_genre(rows, lookup.values) if genre_key(g) == wanted)
        lines = [f"<b>{whose} top {esc(name)} artists</b> · {esc(period.label)}", ""]
    else:
        # Not one of your genres: take it as an artist and show its genres
        artist = (await stats.canonical_subject("artist", await resolve_subject("artist", query, target, lastfm)))[0]
        own = (await metadata.genres([artist], wait=10)).values.get(name_key(artist))
        if not own:
            if query:
                raise UserFacingError(f"<b>{esc(query)}</b> isn't a genre in your top artists, and as an artist it "
                                      "has no genres on Last.fm.")
            raise UserFacingError(f"No genres found for <b>{esc(artist)}</b>.")
        name, wanted = own[0], genre_key(own[0])
        lines = [f"<b>{esc(artist)}</b>: " + ", ".join(esc(g) for g in own), "",
                 f"<b>{whose} top {esc(name)} artists</b> · {esc(period.label)}", ""]

    matching = _artists_in(rows, lookup.values, lambda gs: gs and any(genre_key(g) == wanted for g in gs))
    if matching:
        lines += _ranked("", matching)[1:]
        lines += ["", f"<code>{plural(sum(p for _, p in matching), 'play')} from "
                      f"{plural(len(matching), 'artist')}</code>"]
    else:
        lines.append("None in the top artists for this period.")
    lines += _pending_note(lookup, "genres")
    await message.reply(truncate("\n".join(lines), TELEGRAM_TEXT_LIMIT))


@router.message(Command("country", "from"))
async def country(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database, stats: Stats,
                  metadata: Metadata) -> None:
    """/country: your top artists from a country, or where an artist is from (defaults to what you're playing)."""
    period, args = parse_period((command.args or "").split(), default=ALL_TIME)
    target, rest = await resolve_target(message, args, db, search=True)
    query = " ".join(rest)
    rows = await artist_rows(stats, target, period, limit=100)
    lookup = await _countries(metadata, [a for a, _ in rows])
    whose = "Your" if target.is_self else f"{user_link(target.lastfm_username)}'s"
    known = [(code, name) for code, name, _, _ in by_country(rows, lookup.values)]

    found = match_country(query, known) if query else None
    lines = []
    if found is None:
        artist = (await stats.canonical_subject("artist", await resolve_subject("artist", query, target, lastfm)))[0]
        result = await metadata.countries([artist], wait=15)
        found = result.values.get(name_key(artist))
        if not found:
            if result.pending:
                raise UserFacingError(f"Still looking up <b>{esc(artist)}</b>; try again in a minute.")
            if query:
                raise UserFacingError(f"<b>{esc(query)}</b> isn't a country in your top artists, and MusicBrainz "
                                      "doesn't know where an artist by that name is from.")
            raise UserFacingError(f"MusicBrainz doesn't know where <b>{esc(artist)}</b> is from.")
        lines += [f"<b>{esc(artist)}</b> is from {flag(found[0])} <b>{esc(found[1])}</b>", ""]

    code, name = found
    matching = _artists_in(rows, lookup.values, lambda c: c is not None and c[0] == code)
    lines += _ranked(f"<b>{whose} top artists from {flag(code)} {esc(name)}</b> · {esc(period.label)}", matching)
    if not matching:
        lines.append("None in the top artists for this period.")
    lines += _pending_note(lookup, "countries")
    await message.reply(truncate("\n".join(lines), TELEGRAM_TEXT_LIMIT))


# --- who knows a genre / country --------------------------------------------------------------

async def _people_artists(people: list[User], db: Database, lastfm: LastFmClient
                          ) -> list[tuple[User, list[tuple[str, int]] | None]]:
    """Each person's all-time top artists: from the index when imported, otherwise Last.fm."""
    limit = asyncio.Semaphore(LOOKUPS)

    async def one(user: User):
        if user.id is not None and user.indexed_at is not None:
            rows = await db.top_from_plays(user.id, "artists", 0, 2**62, limit=MEMBER_ARTISTS)
            return user, [(r.name, r.playcount) for r in rows]
        async with limit:
            try:
                top = await lastfm.top(user.lastfm_username, "artists", "overall", limit=MEMBER_ARTISTS)
            except LastFmError:
                return user, None
        return user, [(i.name, i.playcount) for i in top.items]

    return list(await asyncio.gather(*(one(u) for u in people)))


async def _whoknows_meta(message: Message, command: CommandObject, mode: str, people: list[User], scope: str,
                         lastfm: LastFmClient, db: Database, stats: Stats, metadata: Metadata) -> None:
    query = " ".join((command.args or "").split())
    per_person = await _people_artists(people, db, lastfm)

    # Look up the most played artists across everyone first
    totals: dict[str, tuple[str, int]] = {}
    for _, rows in per_person:
        for artist, plays in rows or []:
            seen = totals.get(name_key(artist))
            totals[name_key(artist)] = (artist, plays + (seen[1] if seen else 0))
    artists = [a for a, _ in sorted(totals.values(), key=lambda pair: -pair[1])]
    lookup = await (metadata.genres(artists) if mode == "genre" else _countries(metadata, artists))

    if mode == "genre":
        if query:
            wanted, title = genre_key(query), query.lower()
        else:
            target, _ = await resolve_target(message, [], db)
            artist = (await stats.canonical_subject("artist", await resolve_subject("artist", "", target, lastfm)))[0]
            own = (await metadata.genres([artist], wait=10)).values.get(name_key(artist))
            if not own:
                raise UserFacingError(f"No genres found for <b>{esc(artist)}</b>. Add a genre, e.g. "
                                      "<code>/whoknowsgenre shoegaze</code>.")
            wanted, title = genre_key(own[0]), own[0]

        def matches(value) -> bool:
            return bool(value) and any(genre_key(g) == wanted for g in value)
    else:
        if query:
            all_rows = [(a, p) for a, p in totals.values()]
            found = match_country(query, [(c, n) for c, n, _, _ in by_country(all_rows, lookup.values)])
            if found is None:
                raise UserFacingError(f"Nobody's top artists are from <b>{esc(query)}</b> yet"
                                      + (", but some are still being looked up." if lookup.pending else "."))
        else:
            target, _ = await resolve_target(message, [], db)
            artist = (await stats.canonical_subject("artist", await resolve_subject("artist", "", target, lastfm)))[0]
            found = (await metadata.countries([artist], wait=15)).values.get(name_key(artist))
            if not found:
                raise UserFacingError(f"Don't know where <b>{esc(artist)}</b> is from. Add a country, e.g. "
                                      "<code>/whoknowscountry japan</code>.")
        title = f"{flag(found[0])} {found[1]}".strip()

        def matches(value) -> bool:
            return value is not None and value[0] == found[0]

    entries, failed = [], 0
    for person, rows in per_person:
        if rows is None:
            failed += 1
            continue
        plays = sum(p for a, p in rows if matches(lookup.values.get(name_key(a))))
        if plays:
            entries.append(Entry(person.display_name or person.lastfm_username, person.lastfm_username, plays,
                                 person.telegram_id == message.from_user.id))
    if not entries:
        raise UserFacingError(f"Nobody {scope} listens to <b>{esc(title)}</b> artists"
                              + (" yet; some artists are still being looked up." if lookup.pending else "."))
    entries.sort(key=lambda e: -e.plays)
    notes = [n for n in _pending_note(lookup, "genres" if mode == "genre" else "countries") if n]
    heading = f"<b>Who knows {esc(title)}</b> {scope}"
    await message.reply(truncate(render_board(heading, entries, failed, notes), TELEGRAM_TEXT_LIMIT))


async def _group(message: Message, command: CommandObject, mode: str, lastfm: LastFmClient, db: Database,
                 stats: Stats, settings: Settings, metadata: Metadata) -> None:
    require_group(message, "Who knows")
    members = await db.chat_lastfm_users(message.chat.id, settings.whoknows_max_members)
    if not members:
        raise UserFacingError("Nobody in this chat has connected Last.fm yet. Use /login to get started.")
    scope = f"in {esc(message.chat.title or 'this chat')}"
    await _whoknows_meta(message, command, mode, members, scope, lastfm, db, stats, metadata)


async def _friends(message: Message, command: CommandObject, mode: str, lastfm: LastFmClient, db: Database,
                   stats: Stats, metadata: Metadata) -> None:
    me = await self_user(message, db)
    names = await friend_names(me, db, lastfm)
    if not names:
        raise UserFacingError("You have no friends here yet. Follow people on Last.fm, or add them with "
                              "<code>/addfriend username</code>.")
    registered = await db.users_by_lastfm(names)
    people = [me] + [registered.get(n.lower()) or User(lastfm_username=n) for n in names]
    await _whoknows_meta(message, command, mode, people, "among your friends", lastfm, db, stats, metadata)


@router.message(Command("whoknowsgenre", "wkg"))
async def whoknows_genre(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database,
                         stats: Stats, settings: Settings, metadata: Metadata) -> None:
    await _group(message, command, "genre", lastfm, db, stats, settings, metadata)


@router.message(Command("whoknowscountry", "wkc"))
async def whoknows_country(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database,
                           stats: Stats, settings: Settings, metadata: Metadata) -> None:
    await _group(message, command, "country", lastfm, db, stats, settings, metadata)


@router.message(Command("friendwhoknowsgenre", "fwkgenre", "fwkg"))
async def friend_whoknows_genre(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database,
                                stats: Stats, metadata: Metadata) -> None:
    await _friends(message, command, "genre", lastfm, db, stats, metadata)


@router.message(Command("friendwhoknowscountry", "fwkcountry", "fwkc"))
async def friend_whoknows_country(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database,
                                  stats: Stats, metadata: Metadata) -> None:
    await _friends(message, command, "country", lastfm, db, stats, metadata)
