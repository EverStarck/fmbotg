"""Who knows within a group, among your friends, and across every user of the bot
(port of WhoKnows*Commands, FriendWhoKnows* and GlobalWhoKnows*).

Group and friends: people with an imported history are counted from the play index, the rest
with a live Last.fm lookup each (cached for 5 minutes, concurrency-limited). Global who knows
only covers imported histories, since it can't ask Last.fm about every user.
"""

import html
from dataclasses import dataclass

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from ..config import Settings
from ..context import UserFacingError, require_group, resolve_subject, resolve_target, self_user
from ..crowns import update_crown
from ..db import PRIVACY_GLOBAL, Database, User
from ..formatting import TELEGRAM_TEXT_LIMIT, esc, lastfm_user_url, num, plural, truncate
from ..lastfm import LastFmClient
from ..stats import Stats
from .friends import friend_names

router = Router(name="whoknows")
SHOWN = 15


@dataclass(frozen=True)
class Entry:
    name: str
    lastfm_username: str | None  # None hides the profile link (private users in global who knows)
    plays: int
    is_caller: bool = False
    crown: bool = False


def _line(position: int, entry: Entry) -> str:
    name = esc(entry.name)
    if entry.lastfm_username:
        name = f'<a href="{html.escape(lastfm_user_url(entry.lastfm_username))}">{name}</a>'
    if entry.is_caller:
        name = f"<b>{name}</b>"
    return f"{'👑' if entry.crown else f'{position}.'} {name} · <b>{plural(entry.plays, 'play')}</b>"


def render_board(heading: str, entries: list[Entry], failed: int = 0, notes: list[str] = ()) -> str:
    """Entries must be sorted by plays, highest first, and only include listeners."""
    lines = [heading, ""]
    lines += [_line(position, entry) for position, entry in enumerate(entries[:SHOWN], 1)]
    caller = next(((i, e) for i, e in enumerate(entries, 1) if e.is_caller), None)
    if caller and caller[0] > SHOWN:
        lines += ["…", _line(*caller)]

    total = sum(e.plays for e in entries)
    footer = f"{plural(len(entries), 'listener')} · {plural(total, 'play')} · {num(total // len(entries))} avg"
    if failed:
        footer += f" · {failed} couldn't be fetched"
    lines += ["", f"<code>{footer}</code>"]
    if notes:
        lines += ["", *notes]
    return "\n".join(lines)


async def _subject(message: Message, command: CommandObject, kind: str, lastfm: LastFmClient, db: Database,
                   stats: Stats) -> tuple[str, ...]:
    query = " ".join((command.args or "").split())
    if query:
        # A search was given; the caller doesn't need to be registered.
        subject = await resolve_subject(kind, query, None, lastfm)
    else:
        target, _ = await resolve_target(message, [], db)
        subject = await resolve_subject(kind, "", target, lastfm)
    return await stats.canonical_subject(kind, subject)


def _sorted_listeners(results: list[tuple[User, int | None]]) -> list[tuple[User, int]]:
    return sorted(((m, c) for m, c in results if c), key=lambda pair: pair[1], reverse=True)


# --- group ----------------------------------------------------------------------------------

async def _whoknows(message: Message, command: CommandObject, kind: str, lastfm: LastFmClient, db: Database,
                    stats: Stats, settings: Settings) -> None:
    require_group(message, "Who knows")
    subject = await _subject(message, command, kind, lastfm, db, stats)

    members = await db.chat_lastfm_users(message.chat.id, settings.whoknows_max_members)
    if not members:
        raise UserFacingError("Nobody in this chat has connected Last.fm yet. Use /login to get started.")

    results = await stats.whoknows_counts(kind, subject, members, message.from_user.id)
    holder, announcement = (None, None)
    if kind == "artist":
        holder, announcement = await update_crown(db, message.chat.id, subject[0], results)

    listeners = _sorted_listeners(results)
    title = " - ".join(subject)
    if not listeners:
        raise UserFacingError(f"Nobody here has listened to <b>{esc(title)}</b> yet.")

    entries = [Entry(m.display_name or m.lastfm_username, m.lastfm_username, plays,
                     m.telegram_id == message.from_user.id, m.id == holder) for m, plays in listeners]
    heading = f"<b>Who knows {esc(title)}</b> in {esc(message.chat.title or 'this chat')}"
    failed = sum(1 for _, c in results if c is None)
    await message.reply(truncate(render_board(heading, entries, failed, [announcement] if announcement else []),
                                 TELEGRAM_TEXT_LIMIT))


@router.message(Command("whoknows", "wk", "w"))
async def whoknows_artist(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database,
                          stats: Stats, settings: Settings) -> None:
    await _whoknows(message, command, "artist", lastfm, db, stats, settings)


@router.message(Command("whoknowsalbum", "wka", "wa"))
async def whoknows_album(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database,
                         stats: Stats, settings: Settings) -> None:
    await _whoknows(message, command, "album", lastfm, db, stats, settings)


@router.message(Command("whoknowstrack", "wkt", "wt"))
async def whoknows_track(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database,
                         stats: Stats, settings: Settings) -> None:
    await _whoknows(message, command, "track", lastfm, db, stats, settings)


# --- friends --------------------------------------------------------------------------------

async def _friend_whoknows(message: Message, command: CommandObject, kind: str, lastfm: LastFmClient,
                           db: Database, stats: Stats) -> None:
    me = await self_user(message, db)
    names = await friend_names(me, db, lastfm)
    if not names:
        raise UserFacingError("You have no friends here yet. Follow people on Last.fm, or add them with "
                              "<code>/addfriend lfm:username</code>.")
    subject = await _subject(message, command, kind, lastfm, db, stats)

    registered = await db.users_by_lastfm(names)
    # Friends who never used the bot get a throwaway User so the live lookup can count them
    people = [me] + [registered.get(n.lower()) or User(lastfm_username=n) for n in names]
    results = await stats.whoknows_counts(kind, subject, people, message.from_user.id)

    listeners = _sorted_listeners(results)
    title = " - ".join(subject)
    if not listeners:
        raise UserFacingError(f"None of your friends have listened to <b>{esc(title)}</b>.")
    entries = [Entry(m.lastfm_username, m.lastfm_username, plays, m is me) for m, plays in listeners]
    failed = sum(1 for _, c in results if c is None)
    await message.reply(truncate(render_board(f"<b>Who knows {esc(title)}</b> among your friends", entries, failed),
                                 TELEGRAM_TEXT_LIMIT))


@router.message(Command("friendwhoknows", "friendswhoknows", "fwk", "fw"))
async def friend_whoknows_artist(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database,
                                 stats: Stats) -> None:
    await _friend_whoknows(message, command, "artist", lastfm, db, stats)


@router.message(Command("friendwhoknowsalbum", "fwka", "fwa"))
async def friend_whoknows_album(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database,
                                stats: Stats) -> None:
    await _friend_whoknows(message, command, "album", lastfm, db, stats)


@router.message(Command("friendwhoknowstrack", "fwkt", "fwt"))
async def friend_whoknows_track(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database,
                                stats: Stats) -> None:
    await _friend_whoknows(message, command, "track", lastfm, db, stats)


# --- global ---------------------------------------------------------------------------------

def global_entry(user: User, plays: int, caller_id: int, visible_ids: set[int]) -> Entry:
    """People who chose /privacy global are named; the rest show as "Private user", except to
    themselves and to the groups they're in, where /whoknows shows them anyway."""
    is_caller = user.telegram_id == caller_id
    if is_caller or user.privacy_level == PRIVACY_GLOBAL or user.id in visible_ids:
        return Entry(user.display_name or user.lastfm_username, user.lastfm_username, plays, is_caller)
    return Entry("Private user", None, plays)


async def _global_whoknows(message: Message, command: CommandObject, kind: str, lastfm: LastFmClient,
                           db: Database, stats: Stats, settings: Settings) -> None:
    subject = await _subject(message, command, kind, lastfm, db, stats)
    caller = await db.get_user(message.from_user.id)
    rows = await stats.global_whoknows(kind, subject, caller)
    title = " - ".join(subject)
    if not rows:
        raise UserFacingError(f"Nobody using this bot has listened to <b>{esc(title)}</b> yet "
                              "(only imported histories count).")

    visible: set[int] = set()
    if message.chat.type in ("group", "supergroup"):
        visible = {m.id for m in await db.chat_lastfm_users(message.chat.id, settings.whoknows_max_members)}
    entries = [global_entry(user, plays, message.from_user.id, visible) for user, plays in rows]

    notes = []
    if caller is not None and caller.indexed_at is not None and not any(e.is_caller for e in entries):
        mine = (await db.playcounts(kind, subject, [caller.id])).get(caller.id, 0)
        if mine:
            notes.append(f"You: <b>{plural(mine, 'play')}</b>, outside the top {len(entries)}")
    if caller is None or caller.privacy_level != PRIVACY_GLOBAL:
        notes.append("<i>Your name only shows to other groups with /privacy global.</i>")
    await message.reply(truncate(render_board(f"<b>Who knows {esc(title)}</b> globally", entries, notes=notes),
                                 TELEGRAM_TEXT_LIMIT))


@router.message(Command("globalwhoknows", "gwk", "gw"))
async def global_whoknows_artist(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database,
                                 stats: Stats, settings: Settings) -> None:
    await _global_whoknows(message, command, "artist", lastfm, db, stats, settings)


@router.message(Command("globalwhoknowsalbum", "gwka", "gwa"))
async def global_whoknows_album(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database,
                                stats: Stats, settings: Settings) -> None:
    await _global_whoknows(message, command, "album", lastfm, db, stats, settings)


@router.message(Command("globalwhoknowstrack", "gwkt", "gwt"))
async def global_whoknows_track(message: Message, command: CommandObject, lastfm: LastFmClient, db: Database,
                                stats: Stats, settings: Settings) -> None:
    await _global_whoknows(message, command, "track", lastfm, db, stats, settings)
