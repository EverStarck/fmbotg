"""Who knows within a group, among your friends, and across every user of the bot
(port of WhoKnows*Commands, FriendWhoKnows* and GlobalWhoKnows*).

Group and friends: people with an imported history are counted from the play index, the rest
with a live Last.fm lookup each (cached for 5 minutes, concurrency-limited). Global who knows
only covers imported histories, since it can't ask Last.fm about every user.
"""

from aiogram import Router
from aiogram.filters import CommandObject
from aiogram.types import Message

from ..context import chat_title, require_group, resolve_subject, resolve_target, self_user
from ..domain import UserFacingError
from ..crowns import CrownChange, update_crown
from ..db import PRIVACY_GLOBAL, Database, User
from ..formatting import TELEGRAM_TEXT_LIMIT, esc, plural, truncate
from ..boards import Entry, render_board
from ..lastfm import LastFmClient
from ..members import Members
from ..routing import add_kind_commands
from ..social import friend_roster
from ..stats import Stats

router = Router(name="whoknows")


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

async def _whoknows(message: Message, kind: str, command: CommandObject, lastfm: LastFmClient, db: Database,
                    stats: Stats, members: Members) -> None:
    require_group(message, "Who knows")
    subject = await _subject(message, command, kind, lastfm, db, stats)
    people = await members.require_linked(message.chat.id)
    results = await stats.whoknows_counts(kind, subject, people, message.from_user.id)
    crown = CrownChange()
    if kind == "artist":
        crown = await update_crown(db, message.chat.id, subject[0], results)
    holder, announcement = crown.holder_id, crown.announcement()

    listeners = _sorted_listeners(results)
    title = " - ".join(subject)
    if not listeners:
        raise UserFacingError(f"Nobody here has listened to <b>{esc(title)}</b> yet.")

    entries = [Entry(m.shown_name, m.lastfm_username, plays,
                     m.telegram_id == message.from_user.id, m.id == holder) for m, plays in listeners]
    heading = f"<b>Who knows {esc(title)}</b> in {esc(chat_title(message))}"
    failed = sum(1 for _, c in results if c is None)
    await message.reply(truncate(render_board(heading, entries, failed, [announcement] if announcement else []),
                                 TELEGRAM_TEXT_LIMIT))


add_kind_commands(router, _whoknows, {
    "artist": ("whoknows", "wk", "w"),
    "album": ("whoknowsalbum", "wka", "wa"),
    "track": ("whoknowstrack", "wkt", "wt"),
})


# --- friends --------------------------------------------------------------------------------

async def _friend_whoknows(message: Message, kind: str, command: CommandObject, lastfm: LastFmClient,
                           db: Database, stats: Stats) -> None:
    me = await self_user(message, db)
    people = await friend_roster(me, db, lastfm)
    subject = await _subject(message, command, kind, lastfm, db, stats)
    results = await stats.whoknows_counts(kind, subject, people, message.from_user.id)

    listeners = _sorted_listeners(results)
    title = " - ".join(subject)
    if not listeners:
        raise UserFacingError(f"None of your friends have listened to <b>{esc(title)}</b>.")
    entries = [Entry(m.lastfm_username, m.lastfm_username, plays, m is me) for m, plays in listeners]
    failed = sum(1 for _, c in results if c is None)
    await message.reply(truncate(render_board(f"<b>Who knows {esc(title)}</b> among your friends", entries, failed),
                                 TELEGRAM_TEXT_LIMIT))


add_kind_commands(router, _friend_whoknows, {
    "artist": ("friendwhoknows", "friendswhoknows", "fwk", "fw"),
    "album": ("friendwhoknowsalbum", "fwka", "fwa"),
    "track": ("friendwhoknowstrack", "fwkt", "fwt"),
})


# --- global ---------------------------------------------------------------------------------

def global_entry(user: User, plays: int, caller_id: int, visible_ids: set[int]) -> Entry:
    """People who chose /privacy global are named; the rest show as "Private user", except to
    themselves and to the groups they're in, where /whoknows shows them anyway."""
    is_caller = user.telegram_id == caller_id
    if is_caller or user.privacy_level == PRIVACY_GLOBAL or user.id in visible_ids:
        return Entry(user.shown_name, user.lastfm_username, plays, is_caller)
    return Entry("Private user", None, plays)


async def _global_whoknows(message: Message, kind: str, command: CommandObject, lastfm: LastFmClient,
                           db: Database, stats: Stats, members: Members) -> None:
    subject = await _subject(message, command, kind, lastfm, db, stats)
    caller = await db.get_user(message.from_user.id)
    rows = await stats.global_whoknows(kind, subject, caller)
    title = " - ".join(subject)
    if not rows:
        raise UserFacingError(f"Nobody using this bot has listened to <b>{esc(title)}</b> yet "
                              "(only imported histories count).")

    visible: set[int] = set()
    if message.chat.type in ("group", "supergroup"):
        visible = {m.id for m in await members.linked(message.chat.id)}
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


add_kind_commands(router, _global_whoknows, {
    "artist": ("globalwhoknows", "gwk", "gw"),
    "album": ("globalwhoknowsalbum", "gwka", "gwa"),
    "track": ("globalwhoknowstrack", "gwkt", "gwt"),
})
